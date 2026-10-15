import streamlit as st
from vt_banner import render_vt_banner
import os
import base64
import re
import tempfile
import shutil
import uuid
import time
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError
from anthropic import APIError
from dotenv import load_dotenv
import subprocess

from llm import (
    FailoverLLM,
    LLMProvider,
    TruncatedResponseError,
    arc_provider_from_env,
    claude_provider_from_env,
    verify_provider_key,
)
from usage_metrics import UsageRecord, infer_department, record_conversion
from vt_departments import UNKNOWN_DEPARTMENT

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

APP_LOGO_PATH = Path(__file__).resolve().parent / "images" / "logo.png"

# LLM providers from .env, shared by every session: VT ARC by default,
# Anthropic Claude as the fallback when ARC is unavailable. Each session gets
# its own FailoverLLM (see get_llm) so one session's failover doesn't affect
# others -- and a session with its own ARC key gets its own ARC provider.
ENV_ARC_PROVIDER: LLMProvider | None = arc_provider_from_env()
CLAUDE_PROVIDER: LLMProvider | None = claude_provider_from_env()

# session_state key holding the user's own VT ARC API key ("" if none).
ARC_KEY_STATE: str = 'arc_api_key'

# Page count above which the user is advised to split the PDF.
LARGE_DOCUMENT_PAGES: int = 50

# Prefix for all temp dirs created by this app (used for both creation and reaping)
TEMP_DIR_PREFIX = "noteconv_"

# Maximum age (hours) before an orphaned temp dir is reaped on startup
TEMP_DIR_MAX_AGE_HOURS = float(os.getenv("TEMP_DIR_MAX_AGE_HOURS", "24"))


def reap_stale_temp_dirs(max_age_hours=TEMP_DIR_MAX_AGE_HOURS):
    """Delete orphaned noteconv_* temp dirs older than max_age_hours.

    Streamlit has no reliable session-teardown hook, and temp dirs survive
    process exit, so this startup sweep is the safety net that bounds disk
    usage regardless of crashes, kills, or abandoned sessions.

    Args:
        max_age_hours: Age threshold in hours; dirs older than this are removed.
    """
    temp_root = Path(tempfile.gettempdir())
    cutoff = time.time() - (max_age_hours * 3600)
    for path in temp_root.glob(f"{TEMP_DIR_PREFIX}*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                logger.info(f"Reaped stale temp dir: {path}")
        except OSError as e:
            logger.warning(f"Could not reap stale temp dir {path}: {e}")


# Initialize session state
if 'session_id' not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# Versioned key for the file_uploader; bumped on reset to force a clean remount
if 'uploader_key' not in st.session_state:
    st.session_state.uploader_key = 0

# Sweep old temp dirs once per session (new browser sessions trigger the reaper)
if 'temp_reaper_ran' not in st.session_state:
    reap_stale_temp_dirs()
    st.session_state.temp_reaper_ran = True

def _announce_failover(primary: LLMProvider, fallback: LLMProvider, error: APIError) -> None:
    """Tell the user this session has switched from ARC to the Claude fallback."""
    st.warning(
        f"⚠️ {primary.label} is unavailable ({type(error).__name__}: {error}). "
        f"Switching to {fallback.label} for the rest of this session."
    )


def get_user_arc_key() -> str:
    """Return the ARC API key the user entered in the sidebar, or "" if none."""
    key: str = st.session_state.get(ARC_KEY_STATE, "")
    return key.strip()


def get_llm() -> FailoverLLM:
    """Return this session's LLM, creating it on first use.

    If the user entered their own ARC key, ARC is called with THAT key and the
    .env ARC_API_KEY is not used. The LLM is rebuilt whenever the entered key
    changes, so a new key takes effect on the very next call.

    Stored in session_state so the ARC -> Claude switch is sticky for the
    session; a new key, a Reset, or a new browser session starts on ARC again.

    Raises:
        ValueError: If no ARC key (entered or in .env) and no Claude key exist.
    """
    user_key = get_user_arc_key()
    if 'llm' not in st.session_state or st.session_state.get('llm_key_sig') != user_key:
        arc_provider = arc_provider_from_env(user_key) if user_key else ENV_ARC_PROVIDER
        st.session_state.llm = FailoverLLM(
            primary=arc_provider,
            fallback=CLAUDE_PROVIDER,
            on_failover=_announce_failover,
            # A rejected user key must be reported, not hidden by the fallback.
            fail_over_on_auth_error=not user_key,
        )
        st.session_state.llm_key_sig = user_key
    llm: FailoverLLM = st.session_state.llm
    return llm


def render_arc_key_input() -> None:
    """Sidebar field for the user's own VT ARC API key, with a key check.

    Must run before anything calls get_llm() in a script run, so the key the
    user just typed is the one that run uses.
    """
    user_key = get_user_arc_key()
    with st.expander("🔑 VT ARC API Key", expanded=not user_key):
        # A stable widget key: with value= instead, the widget's ID changes once
        # a key is entered and the user's next edit (e.g. clearing it) is lost.
        if 'arc_api_key_widget' not in st.session_state:
            st.session_state.arc_api_key_widget = user_key
        entered = st.text_input(
            "Your VT ARC API key",
            type="password",
            key='arc_api_key_widget',
            placeholder="sk-…",
            help="Your personal VT ARC key. When set, every AI request in this session uses it instead of the app's shared key.",
        ).strip()
        st.session_state[ARC_KEY_STATE] = entered
        if entered:
            # Verify once per distinct key, not on every rerun.
            if st.session_state.get('arc_key_check_for') != entered:
                provider = arc_provider_from_env(entered)
                assert provider is not None  # a non-blank key always builds one
                try:
                    verify_provider_key(provider)
                    st.session_state.arc_key_check_error = ""
                except APIError as e:
                    st.session_state.arc_key_check_error = f"{type(e).__name__}: {e}"
                st.session_state.arc_key_check_for = entered
            if st.session_state.arc_key_check_error:
                st.error(f"❌ ARC could not verify this key — {st.session_state.arc_key_check_error}")
            else:
                st.success("✓ Key verified — using your key for all AI requests.")
        else:
            st.warning(
                "**Strongly recommended:** enter your own VT ARC key. Without one, "
                "the app's shared key is used."
            )
        st.markdown(
            "**Get your own VT ARC API key**\n"
            "1. Go to [llm.arc.vt.edu](https://llm.arc.vt.edu/)\n"
            "2. Log in with your VT credentials (PID and password)\n"
            "3. Click your profile icon → **Settings** → **Account** → **API Keys**\n"
            "4. Click **Create new secret key**, name it, and click **Create**\n"
            "5. **Copy the key immediately** and paste it above"
        )


def reset_session():
    """Reset the UI to its initial state by clearing all session data.

    Removes the active temp directory, wipes every session_state key, and lets
    the top-of-script initializers recreate a fresh session_id on the next rerun.
    """
    old_dir = st.session_state.get('temp_dir_path')
    if old_dir:
        shutil.rmtree(old_dir, ignore_errors=True)
    # Bump the uploader key so the file_uploader widget remounts empty. Just
    # clearing session_state leaves the widget's own state (and its file) intact.
    next_uploader_key = st.session_state.get('uploader_key', 0) + 1
    # The user's ARC key is a setting, not conversion data: keep it.
    user_arc_key = get_user_arc_key()
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.uploader_key = next_uploader_key
    st.session_state[ARC_KEY_STATE] = user_arc_key
    st.rerun()

def pdf_to_images(pdf_path, max_pages=None):
    """Convert PDF pages to images for Claude vision API."""
    images = convert_from_path(pdf_path, dpi=200)
    if max_pages:
        images = images[:max_pages]
    return images

def image_to_base64(image):
    """Convert PIL Image to base64 string."""
    from io import BytesIO
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def fix_ocr_errors(extracted_text, page_num):
    """Use Claude to fix obvious OCR errors in extracted text.

    Args:
        extracted_text: The initially extracted text from OCR
        page_num: Page number for context

    Returns:
        Corrected text with OCR errors fixed
    """
    prompt = f"""You are reviewing OCR-extracted text from handwritten notes. Your task is to fix ONLY obvious OCR errors while preserving all original content and meaning.

CRITICAL RULES:
- Fix ONLY obvious transcription errors (e.g., "1" vs "l", "0" vs "O", malformed LaTeX)
- Do NOT change, add, or remove any actual content or meaning
- Do NOT "improve" the writing or add explanations
- Do NOT change the structure or organization
- Do NOT add page numbers, headers, or any new content
- Preserve ALL mathematical notation exactly (only fix syntax errors in LaTeX)
- \\tag{{N}} is only valid inside display math ($$...$$); if you see \\tag inside inline math ($...$), rewrite it as plain text (N)
- Keep all {{{{FIGURE:...}}}} markers exactly as they are

Here is the extracted text to review:

{extracted_text}

Return the corrected text wrapped EXACTLY between the markers <CORRECTED> and </CORRECTED>. Output NOTHING outside the markers -- no commentary, no analysis, no explanation of what you changed."""

    # 8192 leaves room for extended thinking on top of a full-page rewrite;
    # create_text_message escalates further if the response still truncates.
    response_text = get_llm().text(
        content=prompt,
        max_tokens=8192,
        purpose=f"page {page_num} OCR correction",
    )
    # Only trust text inside the sentinels. Anything outside them (or a
    # response without them) is commentary that must never reach the document
    # -- an earlier version of this pass leaked "Looking at the text, I
    # notice..." analysis straight into the rendered notes.
    match = re.search(r"<CORRECTED>\s*\n?(.*?)\n?\s*</CORRECTED>", response_text, re.DOTALL)
    if match:
        return match.group(1)
    logger.warning(
        f"OCR correction for page {page_num} returned no <CORRECTED> markers; "
        "keeping the uncorrected extraction."
    )
    return extracted_text


def extract_text_from_page(image, page_num, temp_dir, preserve_figures=True):
    """Use Claude vision API to extract handwritten text from a page image.

    Args:
        image: PIL Image object
        page_num: Page number
        temp_dir: Temporary directory to save image files
        preserve_figures: Whether to save and preserve figure images

    Returns:
        Tuple of (extracted_text, image_references)
        where image_references is a list of (image_filename, description) tuples
    """
    image_b64 = image_to_base64(image)

    if preserve_figures:
        figure_instruction = "- If there are diagrams, drawings, graphs, or figures, mark their location with {{{{FIGURE: brief description}}}} and I will preserve them as images\n- Do NOT attempt to describe complex diagrams in detail - just note their presence and general purpose"
    else:
        figure_instruction = "- If there are diagrams, drawings, graphs, or figures, describe them briefly in plain text as part of the notes"

    prompt = f"""You are analyzing handwritten notes. Please extract ALL text, equations, and content from this image.

IMPORTANT INSTRUCTIONS:
- Preserve the structure and organization of the content
- Convert ALL mathematical expressions and equations to LaTeX format (use $...$ for inline math and $$...$$ for display math)
- Equation numbers: use \\tag{{N}} ONLY inside display math ($$...$$). NEVER put \\tag inside inline math ($...$) -- it is invalid LaTeX there and breaks PDF rendering. If an equation number appears outside display math, write it as plain text (N) instead
- Every $ and $$ must be correctly paired; never leave an unclosed math delimiter
- Identify and preserve any headings, lists, or structured content
- Be thorough and capture all visible text and formulas
- Do NOT add page numbers, headers, or any metadata not present in the image
{figure_instruction}

Please provide the extracted content in a clean, readable format without adding page numbers or headers."""

    extracted_text = get_llm().text(
        content=[
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
            {
                "type": "text",
                "text": prompt
            }
        ],
        max_tokens=8192,
        purpose=f"page {page_num} extraction",
    )

    # Check if there are any figure markers indicating we should preserve the image
    image_references = []
    if preserve_figures:
        has_figures = "{{FIGURE:" in extracted_text or "[FIGURE:" in extracted_text or "diagram" in extracted_text.lower() or "drawing" in extracted_text.lower()

        if has_figures:
            # Save the page image for inclusion in the document
            image_filename = f"figure_{page_num}.png"
            image_path = Path(temp_dir) / image_filename
            image.save(image_path, format="PNG")
            image_references.append((image_filename, "Diagram or drawing from handwritten notes"))

    return extracted_text, image_references

def generate_document_title(pages_content):
    """Generate an appropriate title for the document based on its content.

    Args:
        pages_content: List of tuples (text_content, image_references)

    Returns:
        A concise, descriptive title for the document
    """
    # Combine first few pages of content for analysis (max 2000 chars)
    combined_text = ""
    for content, _ in pages_content[:3]:  # Use first 3 pages
        combined_text += content + "\n"
        if len(combined_text) > 2000:
            combined_text = combined_text[:2000]
            break

    prompt = f"""Based on the following content from handwritten notes, generate a concise, descriptive title (maximum 10 words).

The title should:
- Capture the main topic or subject matter
- Be professional and clear
- Not include words like "Notes", "Handwritten", or "Document" (this is implied)
- Not include page numbers or metadata

Content:
{combined_text}

Please respond with ONLY the title, nothing else."""

    try:
        # A 100-token cap could be swallowed whole by extended thinking on a
        # dense sample, leaving no title text in the response at all.
        title = get_llm().text(
            content=prompt,
            max_tokens=1024,
            purpose="document title",
        ).strip()
        # Remove quotes if present
        title = title.strip('"').strip("'")
        return title
    except (APIError, TruncatedResponseError) as e:
        logger.warning(f"Title generation failed ({e}); using the default title.")
        return "Converted Handwritten Notes"

# Inline math span: a single $...$ that is not part of a $$ display delimiter.
# Kept single-line (no DOTALL) so a stray unmatched $ can't pair across
# display-math blocks or paragraphs.
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)((?:\\.|[^$\\])+?)\$(?!\$)")
# amsmath \tag{...} / \tag*{...}
_TAG_RE = re.compile(r"\\tag\*?\s*\{([^{}]*)\}")
# Leftover figure placeholders: {{FIGURE: ...}} or [FIGURE: ...]
_FIGURE_MARKER_RE = re.compile(r"\{\{\s*FIGURE:[^}]*\}\}|\[\s*FIGURE:[^\]]*\]")


def fix_latex_math_errors(text):
    """Mechanically repair LaTeX constructs known to abort a PDF render.

    amsmath's ``\\tag`` is only legal in display math; the extraction model
    occasionally numbers an equation with a standalone inline span like
    ``$\\tag{17}$``, which makes xelatex fail fatally ("\\tag not allowed
    here"). Inside inline math spans only, rewrite ``\\tag{N}`` to a plain
    parenthesized ``(N)``, which is valid anywhere. Display math ($$...$$) is
    left untouched -- \\tag is correct there.
    """
    def _repl(match):
        return "$" + _TAG_RE.sub(r"(\1)", match.group(1)) + "$"
    return _INLINE_MATH_RE.sub(_repl, text)


def strip_figure_markers(text):
    """Remove leftover ``{{FIGURE: ...}}`` placeholders from extracted text.

    When figures are preserved, the page image is embedded separately, so a
    surviving marker would just render as literal ``{{FIGURE: ...}}`` text.
    """
    return _FIGURE_MARKER_RE.sub("", text)


def sanitize_body_content(text):
    """Mechanically neutralize extracted content that would break the render.

    Deterministic repairs for failure classes the LLM occasionally produces,
    applied to every page regardless of which model pass generated it:

    1. Horizontal rules -> ``***``: Quarto's qmd reader treats a line of
       exactly ``---`` (preceded by a blank line) as the start of an embedded
       YAML metadata block, closed by ``---`` or ``...``. Text caught between
       two such rules gets parsed as YAML -- tokens like ``*ISC`` are read as
       YAML alias references, aborting the render. ``***`` renders identically
       but is never a metadata fence.
    2. ``\\tag`` in inline math -> plain ``(N)`` (see fix_latex_math_errors);
       otherwise xelatex aborts with "\\tag not allowed here".
    3. Leftover ``{{FIGURE: ...}}`` placeholders are stripped (see
       strip_figure_markers).

    Args:
        text: Raw extracted content for a single page.

    Returns:
        The content with render-breaking constructs repaired.
    """
    sanitized_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # A line of only hyphens (>=3) is a thematic break that doubles as a
        # YAML open/close fence; a line of only dots can act as a YAML close.
        if len(stripped) >= 3 and (set(stripped) == {"-"} or set(stripped) == {"."}):
            sanitized_lines.append("***")
        else:
            sanitized_lines.append(line)
    text = "\n".join(sanitized_lines)
    text = fix_latex_math_errors(text)
    text = strip_figure_markers(text)
    return text

# LaTeX special characters, escaped for use in plain text (e.g. \hypersetup).
_LATEX_ESCAPES: dict[int, str] = str.maketrans({
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "_": r"\_",
    "#": r"\#",
    "%": r"\%",
    "&": r"\&",
    "$": r"\$",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
})


def create_quarto_document(pages_content, make_accessible=False, remove_page_breaks=False, document_title=None):
    """Create a Quarto .qmd document from extracted pages.

    Args:
        pages_content: List of tuples (text_content, image_references)
        make_accessible: Whether to add accessibility features
        remove_page_breaks: Whether to remove page breaks between pages
        document_title: Optional custom title for the document
    """
    # Use provided title or default
    if document_title is None:
        document_title = "Converted Handwritten Notes"

    # YAML frontmatter with accessibility options
    yaml_header = f"""---
title: "{document_title}"
author: "Converted from Handwritten PDF"
date: today
lang: en
format:
  html:
    toc: true
    toc-depth: 3
    number-sections: true
  pdf:
    documentclass: article
    keep-tex: true
    number-sections: true
"""

    if make_accessible:
        # Escape special LaTeX characters in title for hypersetup
        safe_title = document_title.translate(_LATEX_ESCAPES)
        yaml_header += f"""    include-in-header:
      text: |
        \\usepackage[utf8]{{inputenc}}
        \\usepackage{{fontspec}}
        \\usepackage{{hyperref}}
        \\hypersetup{{pdfauthor={{Converted from PDF}}, pdftitle={{{safe_title}}}, pdfsubject={{Handwritten Notes}}, pdfkeywords={{notes, handwritten}}, colorlinks=true, bookmarks=true, bookmarksopen=true, bookmarksnumbered=true}}
    pdf-engine: xelatex
  docx:
    toc: true
    number-sections: true
---

"""
    else:
        yaml_header += """    pdf-engine: xelatex
  docx:
    toc: true
---

"""

    # Build document body
    document = yaml_header

    for i, (content, image_refs) in enumerate(pages_content, 1):
        # Add page break between pages (but not before the first page)
        if i > 1 and make_accessible and not remove_page_breaks:
            document += f"{{{{< pagebreak >}}}}\n\n"

        if make_accessible:
            # Add accessibility markers without page reference
            document += f"::: {{.content-section}}\n\n"

        # Add embedded images first if there are any
        if image_refs:
            for img_filename, img_description in image_refs:
                # Use relative path - images are in same directory as .qmd file
                # LaTeX/xelatex doesn't handle absolute Unix paths well
                document += f"![{img_description}]({img_filename}){{width=100%}}\n\n"

        # Add the text content (sanitized so stray horizontal rules in the
        # extracted notes aren't misparsed as embedded YAML metadata blocks)
        document += sanitize_body_content(content) + "\n\n"

        if make_accessible:
            document += ":::\n\n"

    return document

def extract_latex_log_errors(work_dir, qmd_stem, max_lines=60):
    """Pull the actual error lines out of the LaTeX .log left by a failed run.

    Quarto's stderr often buries or truncates the underlying LaTeX error; the
    ``<stem>.log`` file contains the precise message and source line (e.g.
    "! Package amsmath Error: \\tag not allowed here." with "l.397 ..."). Each
    error line starting with ``!`` is captured with a few lines of context so
    the self-heal prompt sees exactly what LaTeX rejected.

    Returns a string ("" if no log or no errors found).
    """
    log_path = Path(work_dir) / f"{qmd_stem}.log"
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    captured = []
    for idx, line in enumerate(lines):
        if line.startswith("!"):
            captured.extend(lines[idx:idx + 8])
            captured.append("...")
        if len(captured) >= max_lines:
            break
    return "\n".join(captured[:max_lines])


def render_quarto(qmd_path, output_format, work_dir, return_error=False):
    """Render Quarto document to specified format.

    Args:
        qmd_path: Path to the .qmd source file.
        output_format: One of "pdf", "docx", "latex".
        work_dir: Working directory for the render.
        return_error: When True, return a ``(output_file, error_text)`` tuple
            and suppress Streamlit error output, so the caller can drive a
            self-healing retry. When False (default), preserve the original
            behavior: surface errors via ``st.error`` and return the path or None.

    Returns:
        The output ``Path`` (or None) when ``return_error`` is False; otherwise a
        ``(Path_or_None, error_text)`` tuple where ``error_text`` is "" on success.
    """
    def _result(output_file, error_text=""):
        return (output_file, error_text) if return_error else output_file

    try:
        if output_format == "pdf":
            cmd = ["quarto", "render", qmd_path, "--to", "pdf"]
        elif output_format == "docx":
            cmd = ["quarto", "render", qmd_path, "--to", "docx"]
        elif output_format == "latex":
            cmd = ["quarto", "render", qmd_path, "--to", "latex"]
        else:
            return _result(None, f"Unsupported output format: {output_format}")

        result = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)

        if result.returncode != 0:
            error_text = (
                f"Quarto rendering error (exit code {result.returncode}):\n"
                f"STDERR:\n{result.stderr}\n"
            )
            if result.stdout:
                error_text += f"STDOUT:\n{result.stdout}\n"
            latex_errors = extract_latex_log_errors(work_dir, Path(qmd_path).stem)
            if latex_errors:
                error_text += f"LATEX LOG ERRORS:\n{latex_errors}\n"
            if not return_error:
                st.error(f"Quarto rendering error (exit code {result.returncode}):")
                st.error(f"STDERR: {result.stderr}")
                if result.stdout:
                    st.error(f"STDOUT: {result.stdout}")
            return _result(None, error_text)

        # Find the output file - Quarto may sanitize the filename
        # So we need to search for the actual output file instead of guessing
        qmd_stem = Path(qmd_path).stem
        work_dir_path = Path(work_dir)

        # Determine expected extension
        if output_format == "pdf":
            extension = ".pdf"
        elif output_format == "docx":
            extension = ".docx"
        elif output_format == "latex":
            extension = ".tex"
        else:
            return _result(None, f"Unsupported output format: {output_format}")

        # Find the output file by checking modification time
        # Quarto creates/overwrites files after the .qmd file
        qmd_mtime = Path(qmd_path).stat().st_mtime
        output_file = work_dir_path / f"{qmd_stem}{extension}"

        if output_file.exists():
            file_mtime = output_file.stat().st_mtime
            # If the output file was modified after (or at the same time as) the qmd file, it's the rendered version
            if file_mtime >= qmd_mtime:
                return _result(output_file)

        # If not found with expected name, search for any file with the right extension
        candidates = []
        all_matching_files = list(work_dir_path.glob(f"*{extension}"))

        for file in all_matching_files:
            file_mtime = file.stat().st_mtime
            # Must be modified after the qmd file (which means it's newly rendered)
            if file_mtime >= qmd_mtime:
                candidates.append(file)

        if candidates:
            # Return the most recently modified file
            output_file = max(candidates, key=lambda f: f.stat().st_mtime)
            return _result(output_file)
        else:
            all_files = list(work_dir_path.glob("*"))
            error_text = (
                f"Output file not found in {work_dir_path}\n"
                f"Looking for files with extension: {extension}\n"
                f"Files in directory: {[f.name for f in all_files]}"
            )
            if not return_error:
                st.error(f"Output file not found in {work_dir_path}")
                st.error(f"Looking for files with extension: {extension}")
                st.error(f"Files in directory: {[f.name for f in all_files]}")
            return _result(None, error_text)

    except Exception as e:
        if not return_error:
            st.error(f"Error rendering Quarto document: {str(e)}")
        return _result(None, f"Error rendering Quarto document: {str(e)}")

# Maximum LLM repair attempts for one failing page, and for the final document.
SELF_HEAL_MAX_ATTEMPTS: int = 5

# Render errors can carry pages of LaTeX chatter; the tail holds the failure.
HEAL_ERROR_MAX_CHARS: int = 12000

# Filename stem prefix for the throwaway single-page test renders.
PAGE_TEST_PREFIX: str = "_pagetest_"

PageRenderStatus = Literal["passed", "failed", "untested"]
ImageRefs = list[tuple[str, str]]
ProgressCallback = Callable[[str], None]


@dataclass
class ConvertedDocument:
    """Everything needed to regenerate the .qmd after a page is repaired.

    Attributes:
        pages: Per-page (extracted text, image references).
        page_status: Per-page result of the single-page test render.
        make_accessible: Accessibility option the document was built with.
        remove_page_breaks: Page-break option the document was built with.
        title: Document title.
    """

    pages: list[tuple[str, ImageRefs]]
    page_status: list[PageRenderStatus]
    make_accessible: bool
    remove_page_breaks: bool
    title: str

    def to_qmd(self) -> str:
        """Build the full Quarto document from the current page texts."""
        return create_quarto_document(
            self.pages, self.make_accessible, self.remove_page_breaks, self.title
        )


@dataclass
class PageHealResult:
    """Outcome of test-rendering (and, if needed, repairing) one page.

    Attributes:
        text: The page text to use -- repaired if healing succeeded, the
            original if it did not.
        status: "passed" if the page renders, "failed" if every attempt failed.
        heal_attempts: Number of LLM repair attempts made (0 if it rendered
            first time).
        error: The last render error when status is "failed", else "".
        notes: One line per repair attempt that did not succeed.
    """

    text: str
    status: PageRenderStatus
    heal_attempts: int
    error: str
    notes: list[str]


def _trim_error(error_text: str) -> str:
    """Keep the tail of a render error, where the actual failure is reported."""
    if len(error_text) <= HEAL_ERROR_MAX_CHARS:
        return error_text
    return "[...earlier output trimmed...]\n" + error_text[-HEAL_ERROR_MAX_CHARS:]


def _strip_code_fence(text: str) -> str:
    """Strip an accidental ```/```quarto code fence wrapping a whole response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]  # drop opening fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def self_heal_qmd(qmd_content: str, error_text: str) -> str | None:
    """Ask the LLM to repair a full Quarto document that failed to render.

    The whole document must come back in one response, so a very long
    document can exceed the output budget -- a truncated answer raises rather
    than being written back as a half-document.

    Args:
        qmd_content: The full source of the .qmd document that failed to render.
        error_text: The Quarto/LaTeX error output captured from the failed render.

    Returns:
        The corrected .qmd content, or None if the response was empty.

    Raises:
        anthropic.APIError: If the repair request fails.
        TruncatedResponseError: If the repaired document didn't fit in the
            provider's output budget.
    """
    prompt = f"""A Quarto document failed to render to PDF. Below is the full \
.qmd source followed by the exact error output from the render. Diagnose the \
cause and return a corrected version of the entire document that will render \
successfully.

Rules:
- Fix ONLY what is needed to make the render succeed (e.g. malformed YAML, \
unescaped LaTeX special characters, broken Markdown, stray fences, invalid \
math). Preserve all original content, headings, equations, and meaning.
- Keep the YAML frontmatter structure and output formats intact.
- Return the COMPLETE corrected .qmd document and nothing else. Do not wrap it \
in code fences or add explanations.

=== RENDER ERROR ===
{_trim_error(error_text)}

=== QMD SOURCE ===
{qmd_content}
"""
    fixed = _strip_code_fence(get_llm().text(
        content=prompt,
        max_tokens=32000,
        purpose="qmd render repair",
        allow_truncated=False,
    ))
    return fixed or None


def self_heal_page(page_text: str, error_text: str, page_num: int) -> str | None:
    """Ask the LLM to repair one page's content so it renders to PDF.

    Args:
        page_text: The page's extracted Markdown/LaTeX content.
        error_text: The error from rendering that page as a standalone document.
        page_num: Page number, for logging.

    Returns:
        The corrected page content, or None if the response lacked the
        <FIXED> markers.

    Raises:
        anthropic.APIError: If the repair request fails.
        TruncatedResponseError: If the repaired page didn't fit in the
            provider's output budget.
    """
    prompt = f"""One page of handwritten notes, already converted to Quarto \
Markdown with LaTeX math, failed to render to PDF when wrapped in a standalone \
Quarto document. Below is the page content followed by the exact render error. \
Diagnose the cause and return a corrected version of the page content.

Rules:
- Fix ONLY what is needed to make the render succeed (e.g. unpaired $ or $$ \
delimiters, invalid or undefined LaTeX commands and environments, \\tag inside \
inline math, unescaped special characters such as % & # _ outside math, \
Markdown that Pandoc misreads). Preserve all content, headings, equations, \
and meaning.
- This is page body content only: do NOT add YAML frontmatter, a title, or \
code fences.
- Line numbers in the error refer to the generated LaTeX file, not to the \
page content.
- Return the corrected page wrapped EXACTLY between the markers <FIXED> and \
</FIXED>. Output NOTHING outside the markers.

=== RENDER ERROR ===
{_trim_error(error_text)}

=== PAGE CONTENT ===
{page_text}
"""
    response_text = get_llm().text(
        content=prompt,
        max_tokens=16000,
        purpose=f"page {page_num} render repair",
        allow_truncated=False,
    )
    match = re.search(r"<FIXED>\s*\n?(.*?)\n?\s*</FIXED>", response_text, re.DOTALL)
    if match is None:
        logger.warning(f"Page {page_num} repair returned no <FIXED> markers.")
        return None
    return match.group(1)


def _cleanup_page_test(work_dir: Path, stem: str) -> None:
    """Remove every file a single-page test render left behind."""
    for path in work_dir.glob(f"{stem}.*"):
        path.unlink(missing_ok=True)
    shutil.rmtree(work_dir / f"{stem}_files", ignore_errors=True)


def trial_render_page(
    page_text: str,
    image_refs: ImageRefs,
    page_num: int,
    work_dir: Path,
    make_accessible: bool,
) -> tuple[bool, str]:
    """Render one page on its own as a PDF, to catch errors before assembly.

    The page is wrapped in the same YAML header as the final document and
    rendered in the document's working directory, so figure images resolve.
    Every artifact of the test render is deleted afterwards.

    Returns:
        (True, "") if the page rendered, else (False, render error text).
    """
    stem = f"{PAGE_TEST_PREFIX}{page_num}"
    qmd_path = work_dir / f"{stem}.qmd"
    qmd_path.write_text(
        create_quarto_document(
            [(page_text, image_refs)], make_accessible, True, f"Page {page_num} test render"
        ),
        encoding="utf-8",
    )
    try:
        output, error = render_quarto(str(qmd_path), "pdf", str(work_dir), return_error=True)
    finally:
        _cleanup_page_test(work_dir, stem)
    return output is not None, error


def heal_page_until_renders(
    page_text: str,
    image_refs: ImageRefs,
    page_num: int,
    work_dir: Path,
    make_accessible: bool,
    on_progress: ProgressCallback,
) -> PageHealResult:
    """Test-render a page; if it fails, repair it with the LLM up to 5 times.

    If no attempt produces a page that renders, the ORIGINAL text is kept (an
    unsuccessful repair could have dropped content) and the page is reported
    as failed.
    """
    rendered, error = trial_render_page(page_text, image_refs, page_num, work_dir, make_accessible)
    if rendered:
        return PageHealResult(page_text, "passed", 0, "", [])

    notes: list[str] = []
    current = page_text
    for attempt in range(1, SELF_HEAL_MAX_ATTEMPTS + 1):
        on_progress(
            f"🩹 Page {page_num} failed to render — self-healing attempt "
            f"{attempt}/{SELF_HEAL_MAX_ATTEMPTS} with {get_llm().active.label}..."
        )
        try:
            fixed = self_heal_page(current, error, page_num)
        except (APIError, TruncatedResponseError) as e:
            notes.append(f"Attempt {attempt}: repair request failed ({type(e).__name__}: {e})")
            continue
        if fixed is None:
            notes.append(f"Attempt {attempt}: the model did not return a repaired page.")
            continue
        if fixed == current:
            notes.append(f"Attempt {attempt}: the model returned the page unchanged.")
            continue
        current = fixed
        rendered, error = trial_render_page(current, image_refs, page_num, work_dir, make_accessible)
        if rendered:
            return PageHealResult(current, "passed", attempt, "", notes)
        notes.append(f"Attempt {attempt}: the repaired page still failed to render.")
    return PageHealResult(page_text, "failed", SELF_HEAL_MAX_ATTEMPTS, error, notes)


def render_pdf_with_healing(
    document: ConvertedDocument,
    qmd_path: Path,
    work_dir: Path,
    on_progress: ProgressCallback,
) -> tuple[Path | None, str, str, list[str]]:
    """Render the final PDF, self-healing on failure.

    Healing runs in two stages, page-level first because a small, targeted
    repair is cheaper and less likely to disturb unrelated content:

    1. Page by page: every page not yet test-rendered is rendered on its own
       and repaired (up to 5 attempts each) if it fails. The document is then
       rebuilt and rendered again.
    2. Whole document: if it still fails (e.g. every page renders alone but the
       assembly does not), the full .qmd is sent for repair, up to 5 attempts.

    ``document.pages`` and ``document.page_status`` are updated in place, and
    the .qmd on disk is rewritten whenever a repair is applied.

    Returns:
        (pdf path or None, final .qmd content, last render error, notes), where
        notes describe repair attempts that did not succeed.
    """
    qmd_content = qmd_path.read_text(encoding="utf-8")
    pdf, error = render_quarto(str(qmd_path), "pdf", str(work_dir), return_error=True)
    if pdf:
        return pdf, qmd_content, "", []

    notes: list[str] = []

    # Stage 1: locate and repair failing pages one at a time.
    untested = [i for i, status in enumerate(document.page_status) if status == "untested"]
    pages_changed = False
    for i in untested:
        text, image_refs = document.pages[i]
        on_progress(f"🔍 Test-rendering page {i + 1} to locate the failure...")
        result = heal_page_until_renders(
            text, image_refs, i + 1, work_dir, document.make_accessible, on_progress
        )
        document.page_status[i] = result.status
        notes.extend(f"Page {i + 1} — {note}" for note in result.notes)
        if result.text != text:
            document.pages[i] = (result.text, image_refs)
            pages_changed = True
    if pages_changed:
        qmd_content = document.to_qmd()
        qmd_path.write_text(qmd_content, encoding="utf-8")
        pdf, error = render_quarto(str(qmd_path), "pdf", str(work_dir), return_error=True)
        if pdf:
            return pdf, qmd_content, "", notes

    # Stage 2: repair the assembled document as a whole.
    for attempt in range(1, SELF_HEAL_MAX_ATTEMPTS + 1):
        on_progress(
            f"🩹 Self-healing the full document — attempt {attempt}/"
            f"{SELF_HEAL_MAX_ATTEMPTS} with {get_llm().active.label}..."
        )
        try:
            fixed = self_heal_qmd(qmd_content, error)
        except TruncatedResponseError as e:
            # A document too long for one response will be too long next time too.
            notes.append(
                f"Document — attempt {attempt}: the repaired document did not fit in "
                f"the model's output limit ({e}). Stopping whole-document repair."
            )
            break
        except APIError as e:
            notes.append(f"Document — attempt {attempt}: repair request failed ({type(e).__name__}: {e})")
            continue
        if fixed is not None:
            # Mechanically repair known render-killers in the healed document
            # too. (Not sanitize_body_content -- its horizontal-rule rewrite
            # would clobber the YAML frontmatter fences of a full document.)
            fixed = strip_figure_markers(fix_latex_math_errors(fixed))
        if not fixed or fixed == qmd_content:
            notes.append(f"Document — attempt {attempt}: the model returned no change.")
            continue
        qmd_content = fixed
        qmd_path.write_text(qmd_content, encoding="utf-8")
        pdf, error = render_quarto(str(qmd_path), "pdf", str(work_dir), return_error=True)
        if pdf:
            return pdf, qmd_content, "", notes
        notes.append(f"Document — attempt {attempt}: the repaired document still failed to render.")

    return None, qmd_content, error, notes

def check_quarto_installation():
    """Check if Quarto is installed and working."""
    try:
        result = subprocess.run(["quarto", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, "Quarto command failed"
    except FileNotFoundError:
        return False, "Quarto not found in PATH"
    except Exception as e:
        return False, str(e)

def check_adobe_credentials():
    """Check if Adobe PDF Services credentials are configured."""
    client_id = os.getenv('PDF_SERVICES_CLIENT_ID')
    client_secret = os.getenv('PDF_SERVICES_CLIENT_SECRET')
    return bool(client_id and client_secret)


def autotag_pdf_with_adobe(pdf_bytes):
    """Apply Adobe PDF Services Auto-Tag API to add accessibility tags to a PDF.

    Args:
        pdf_bytes: Raw PDF content as bytes

    Returns:
        Tagged PDF bytes, or original bytes if tagging fails
    """
    try:
        from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
        from adobe.pdfservices.operation.exception.exceptions import ServiceApiException, ServiceUsageException, SdkException
        from adobe.pdfservices.operation.pdf_services import PDFServices
        from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
        from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import AutotagPDFJob
        from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import AutotagPDFParams
        from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import AutotagPDFResult
    except ImportError as e:
        logger.warning(f"Adobe PDF Services SDK not available: {e}")
        st.warning("Adobe Auto-Tag unavailable (SDK not installed). Returning untagged PDF.")
        return pdf_bytes

    try:
        credentials = ServicePrincipalCredentials(
            client_id=os.getenv('PDF_SERVICES_CLIENT_ID'),
            client_secret=os.getenv('PDF_SERVICES_CLIENT_SECRET')
        )

        pdf_services = PDFServices(credentials=credentials)

        input_asset = pdf_services.upload(
            input_stream=pdf_bytes,
            mime_type=PDFServicesMediaType.PDF
        )

        autotag_params = AutotagPDFParams(
            generate_report=True,
            shift_headings=False
        )

        autotag_job = AutotagPDFJob(
            input_asset=input_asset,
            autotag_pdf_params=autotag_params
        )

        location = pdf_services.submit(autotag_job)
        pdf_services_response = pdf_services.get_job_result(location, AutotagPDFResult)

        result_asset = pdf_services_response.get_result().get_tagged_pdf()
        stream_asset = pdf_services.get_content(result_asset)
        result_stream = stream_asset.get_input_stream()
        if isinstance(result_stream, bytes):
            return result_stream
        return result_stream.read()

    except (ServiceApiException, ServiceUsageException) as e:
        error_str = str(e)
        if "QUOTA_EXCEEDED" in error_str or "quota" in error_str.lower():
            logger.info(f"Adobe Auto-Tag skipped: quota exhausted")
        else:
            logger.error(f"Adobe PDF Services API error: {e}")
            st.warning(f"Adobe Auto-Tag failed (API error): {e}. Returning untagged PDF.")
        return pdf_bytes

    except SdkException as e:
        logger.error(f"Adobe SDK error: {e}")
        st.warning(f"Adobe Auto-Tag failed (SDK error): {e}. Returning untagged PDF.")
        return pdf_bytes

    except Exception as e:
        logger.error(f"Unexpected error during Adobe Auto-Tag: {e}", exc_info=True)
        st.warning(f"Adobe Auto-Tag failed: {e}. Returning untagged PDF.")
        return pdf_bytes


def main():
    st.set_page_config(
        page_title="Notes Converter - Convert Handwritten Notes to Accessible Documents",
        page_icon=str(APP_LOGO_PATH),
        layout="wide",
        initial_sidebar_state="expanded"
    )
    render_vt_banner(app_icon_path=APP_LOGO_PATH, app_icon_alt="AI Notes Converter")
    logo_b64 = base64.b64encode(APP_LOGO_PATH.read_bytes()).decode("ascii")
    st.markdown(
        '<div style="display: flex; align-items: center; gap: 18px; margin-bottom: 0.5rem;">'
        f'<img src="data:image/png;base64,{logo_b64}" alt="AI Notes Converter logo" style="height: 72px;">'
        '<h1 style="margin: 0; padding: 0;">Handwritten Notes Converter</h1>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown("""
    Upload your handwritten PDF notes to convert them to accessible digital formats including Quarto, LaTeX, Word and PDF documents.

    **This tool uses AI to:**
    - Extract handwritten text from PDF pages
    - Convert mathematical notation to LaTeX format
    - Preserve diagrams and drawings as embedded images with descriptions
    - Generate accessible documents optimized for screen readers
    """)

    # Add accessibility note
    st.info("♿ This application creates documents with accessibility features enabled by default, including semantic structure, alt text for images, and proper heading hierarchy.")

    # Check Quarto installation (silently - only warn if missing)
    quarto_ok, quarto_info = check_quarto_installation()
    if not quarto_ok:
        st.error(f"❌ Quarto Installation Error: {quarto_info}")
        st.warning("⚠️ PDF, Word, and LaTeX downloads require Quarto to be installed. Only .qmd format will be available.")

    # Sidebar options
    with st.sidebar:
        render_arc_key_input()

        st.header("Conversion Options")
        st.markdown("Configure how your document will be processed and formatted.")

        make_accessible = st.checkbox(
            "Enable Accessibility Features",
            value=True,
            help="Adds WCAG-compliant accessibility features including ARIA labels, semantic structure, proper heading hierarchy, and metadata for screen readers and assistive technologies."
        )

        preserve_figures = st.checkbox(
            "Preserve Figures as Images",
            value=True,
            help="Saves diagrams, drawings, and graphs as embedded images with descriptive alt text. When disabled, figures will be described in plain text format."
        )

        enable_ocr_correction = st.checkbox(
            "Enable OCR Error Correction",
            value=True,
            help="Uses AI to perform a second pass that fixes common OCR errors like misread characters (1 vs l, 0 vs O). Note: This doubles processing time."
        )

        remove_page_breaks = st.checkbox(
            "Remove Page Breaks",
            value=True,
            help="Creates a continuous document by removing page break markers between scanned pages. Disable to preserve original page structure."
        )

        test_render_pages = st.checkbox(
            "Test Render Each Page",
            value=True,
            disabled=not quarto_ok,
            help=f"Renders each page to PDF on its own as soon as it is extracted. If a page fails to render, the AI repairs it (up to {SELF_HEAL_MAX_ATTEMPTS} attempts) before moving on. Catches broken LaTeX early, at the cost of a few seconds per page. Requires Quarto."
        ) and quarto_ok

        # Adobe Auto-Tag option (DISABLED -- requires a paid Adobe PDF Services
        # account). Left in the codebase, commented out, so it can be re-enabled
        # by restoring this block, the call site in the PDF render section, and
        # the pdfservices-sdk dependency in requirements.txt.
        enable_autotag = False
        # adobe_available = check_adobe_credentials()
        # enable_autotag = st.checkbox(
        #     "Adobe PDF Auto-Tag",
        #     value=adobe_available,
        #     disabled=not adobe_available,
        #     help="Uses Adobe PDF Services API to add production-grade accessibility tags (PDF/UA) to the rendered PDF (very limited budget). "
        #          "Requires PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET in .env file."
        # )
        # if not adobe_available:
        #     st.caption("Adobe credentials not configured. Set PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET in .env")

        st.markdown("---")
        st.markdown("### About This Tool")
        st.markdown("""
        This application uses Virginia Tech ARC hosted vision AI (falling back to Anthropic Claude if ARC is unavailable) to:
        - **Extract** handwritten text from PDF pages with high accuracy
        - **Convert** mathematical notation to accessible LaTeX format
        - **Preserve** diagrams and drawings as embedded images with alt text
        - **Generate** multiple accessible output formats (PDF, Word, LaTeX, Quarto)

        **Accessibility Commitment:**
        Output documents comply with WCAG 2.1 Level AA standards when "Enable Accessibility Features" is checked.
        """)

        st.markdown("---")
        try:
            st.caption(f"🤖 AI model: {get_llm().active.label}")
        except ValueError as e:
            st.error(f"❌ {e}")
        if st.button(
            "🔄 Reset",
            help="Clear the uploaded file, conversion results, and all session data, returning the app to its initial state.",
            use_container_width=True
        ):
            reset_session()

    # File upload
    st.markdown("### Step 1: Upload Your Document")
    uploaded_file = st.file_uploader(
        "Select a PDF file containing handwritten notes to convert",
        type=["pdf"],
        help="Upload a PDF file with handwritten notes. The file will be processed page-by-page to extract text, equations, and figures.",
        key=f"pdf_uploader_{st.session_state.uploader_key}"
    )

    if uploaded_file is not None:
        # Extract original filename without extension
        original_filename = Path(uploaded_file.name).stem

        # Switching to a new file: clear cached outputs and remove the old temp
        # dir, then create exactly one fresh temp dir for this file. This runs
        # only when the uploaded file changes -- NOT on every Streamlit rerun --
        # so interactions no longer orphan a new temp dir each click.
        if st.session_state.get('active_filename') != original_filename:
            # Remove the old temp dir FIRST, while the key still points to it
            old_dir = st.session_state.get('temp_dir_path')
            if old_dir:
                shutil.rmtree(old_dir, ignore_errors=True)
            # Clear previous file's cached data and tracking keys
            for key in ['qmd_content', 'qmd_path', 'temp_dir_path', 'pdf_data',
                        'docx_data', 'tex_data', 'conversion_complete', 'original_filename',
                        'pdf_render_failed', 'document']:
                st.session_state.pop(key, None)
            # Create the session/file-specific temp directory exactly once
            session_temp_dir = tempfile.mkdtemp(
                prefix=f"{TEMP_DIR_PREFIX}{st.session_state.session_id[:8]}_"
            )
            st.session_state.temp_dir_path = session_temp_dir
            st.session_state.active_filename = original_filename

        # Reuse the existing temp dir across reruns for the same file
        temp_dir_path = Path(st.session_state.temp_dir_path)

        try:
            # Save uploaded file with original name
            pdf_path = temp_dir_path / f"{original_filename}.pdf"
            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.success(f"✅ File uploaded successfully: {uploaded_file.name}")

            # Very long documents are prone to render failures; suggest splitting.
            # The page count is advisory only, so a PDF pdfinfo can't read just
            # skips the warning (conversion reports its own error if it fails).
            try:
                page_count = int(pdfinfo_from_path(str(pdf_path))["Pages"])
            except (PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError, KeyError, ValueError) as e:
                logger.warning(f"Could not read page count of {pdf_path.name}: {e}")
                page_count = 0
            if page_count > LARGE_DOCUMENT_PAGES:
                st.warning(
                    f"📚 **This document has {page_count} pages.** Documents over "
                    f"{LARGE_DOCUMENT_PAGES} pages are prone to rendering failures — "
                    "consider splitting it into smaller documents. You can use print "
                    "to PDF to print pages 1-50, 51-100, etc."
                )

            # Process button
            st.markdown("### Step 2: Start Conversion")
            if st.button("🔄 Convert to Accessible Document", type="primary", help="Begin processing the uploaded PDF file"):
                with st.spinner("Converting PDF pages to images..."):
                    images = pdf_to_images(str(pdf_path))
                    st.info(f"📄 Found {len(images)} pages to process")

                # Extract text from each page
                pages_content: list[tuple[str, ImageRefs]] = []
                page_status: list[PageRenderStatus] = []
                page_results: list[tuple[int, PageHealResult]] = []
                progress_bar = st.progress(0, text="Starting conversion...")
                heal_status = st.empty()

                def show_heal_progress(message: str) -> None:
                    heal_status.info(message)

                for i, image in enumerate(images):
                    progress_text = f"Processing page {i+1} of {len(images)}"
                    with st.spinner(f"🔍 Extracting text from page {i+1}/{len(images)}..."):
                        text_content, image_refs = extract_text_from_page(image, i+1, temp_dir_path, preserve_figures)

                        # Apply OCR error correction if enabled
                        if enable_ocr_correction:
                            with st.spinner(f"✏️ Correcting OCR errors on page {i+1}/{len(images)}..."):
                                text_content = fix_ocr_errors(text_content, i+1)

                    # Test-render the page on its own, self-healing if it fails
                    if test_render_pages:
                        with st.spinner(f"🧪 Test rendering page {i+1}/{len(images)}..."):
                            page_result = heal_page_until_renders(
                                text_content, image_refs, i+1, temp_dir_path,
                                make_accessible, show_heal_progress,
                            )
                        heal_status.empty()
                        text_content = page_result.text
                        page_status.append(page_result.status)
                        if page_result.heal_attempts:
                            page_results.append((i+1, page_result))
                    else:
                        page_status.append("untested")

                    pages_content.append((text_content, image_refs))
                    progress_bar.progress((i + 1) / len(images), text=f"{progress_text} - Complete")

                st.success(f"✅ Successfully extracted text from all {len(images)} pages")

                if test_render_pages:
                    healed = [n for n, r in page_results if r.status == "passed"]
                    failed = [(n, r) for n, r in page_results if r.status == "failed"]
                    if not page_results:
                        st.success("✅ Every page passed its test render.")
                    if healed:
                        st.info(f"🩹 Self-healing repaired page(s) {', '.join(map(str, healed))} so they render.")
                    if failed:
                        st.warning(
                            f"⚠️ Page(s) {', '.join(str(n) for n, _ in failed)} still failed to render "
                            f"after {SELF_HEAL_MAX_ATTEMPTS} self-healing attempts; their original text "
                            "was kept. The final PDF may need further repair."
                        )
                        with st.expander("🔎 Show page render errors"):
                            for n, r in failed:
                                st.markdown(f"**Page {n}**")
                                for note in r.notes:
                                    st.markdown(f"- {note}")
                                st.code(r.error or "No error details available.")

                # Generate document title
                with st.spinner("🤔 Generating document title..."):
                    document_title = generate_document_title(pages_content)
                    st.info(f"📝 Document title: \"{document_title}\"")

                # Create Quarto document
                with st.spinner("📝 Creating accessible Quarto document..."):
                    document = ConvertedDocument(
                        pages=pages_content,
                        page_status=page_status,
                        make_accessible=make_accessible,
                        remove_page_breaks=remove_page_breaks,
                        title=document_title,
                    )
                    qmd_content = document.to_qmd()
                    qmd_path = temp_dir_path / f"{original_filename}.qmd"
                    with open(qmd_path, "w", encoding="utf-8") as f:
                        f.write(qmd_content)

                # Store in session state to persist across reruns. Clear any
                # cached renders and the self-heal flag from a prior conversion
                # so the new document renders fresh.
                for key in ['pdf_data', 'docx_data', 'tex_data', 'pdf_render_failed']:
                    st.session_state.pop(key, None)
                st.session_state.qmd_content = qmd_content
                st.session_state.document = document
                st.session_state.qmd_path = str(qmd_path)
                st.session_state.temp_dir_path = str(temp_dir_path)
                st.session_state.original_filename = original_filename
                st.session_state.conversion_complete = True

                # Record usage metrics (timestamp, PDF/page counts, and the
                # VT department inferred by the LLM from the extracted text).
                # A metrics failure must not fail an otherwise-successful
                # conversion, so API and file errors are surfaced as warnings.
                with st.spinner("📊 Recording usage metrics..."):
                    notes_sample = "\n".join(
                        content for content, _ in pages_content[:3]
                    )[:4000]
                    try:
                        department = infer_department(get_llm(), notes_sample)
                    except (APIError, TruncatedResponseError) as e:
                        department = UNKNOWN_DEPARTMENT
                        st.warning(
                            f"⚠️ Department inference failed ({e}); "
                            f"recording department as '{UNKNOWN_DEPARTMENT}'."
                        )
                    try:
                        record_conversion(UsageRecord(
                            timestamp=datetime.now(),
                            num_pdfs=1,
                            total_pages=len(images),
                            department=department,
                        ))
                    except OSError as e:
                        st.warning(f"⚠️ Could not record usage metrics: {e}")

                st.success("✅ Conversion complete! Your accessible document is ready for download.")

            # Show download options if conversion is complete
            if st.session_state.get('conversion_complete', False) and st.session_state.get('original_filename') == original_filename:
                # Display preview in expander
                with st.expander("📄 Preview Document Content", expanded=False):
                    st.markdown("**Note:** This is the raw Quarto markdown that will be converted to your chosen format.")
                    st.code(st.session_state.qmd_content, language="markdown")

                # Download section
                st.markdown("---")
                st.markdown("### Step 3: Download Your Document")
                st.markdown("Choose one or more formats to download your converted accessible document.")

                # Render the PDF (self-healing on failure) BEFORE building the
                # download columns. A successful repair rewrites the .qmd on disk
                # and in session_state, so the .qmd/.tex/.docx outputs below all
                # reflect the healed document. If healing fails, the raw .qmd (and
                # any salvageable .tex) are still offered below.
                if 'pdf_data' not in st.session_state and not st.session_state.get('pdf_render_failed'):
                    heal_status = st.empty()

                    def show_render_progress(message: str) -> None:
                        heal_status.info(message)

                    with st.spinner("🔄 Rendering PDF document..."):
                        pdf_output, healed_qmd, render_error, heal_notes = render_pdf_with_healing(
                            st.session_state.document,
                            Path(st.session_state.qmd_path),
                            Path(st.session_state.temp_dir_path),
                            show_render_progress,
                        )
                    heal_status.empty()

                    if healed_qmd != st.session_state.qmd_content:
                        # The document was repaired: drop stale derived outputs
                        # so every format re-renders from the fix.
                        st.session_state.qmd_content = healed_qmd
                        for k in ['docx_data', 'tex_data']:
                            st.session_state.pop(k, None)
                        if pdf_output:
                            st.success("✅ Self-healing succeeded — the document was repaired and rendered to PDF.")

                    if not pdf_output:
                        st.session_state.pdf_render_failed = True
                        st.error(
                            "❌ PDF rendering still failed after self-healing (page by page, then "
                            f"up to {SELF_HEAL_MAX_ATTEMPTS} whole-document attempts). "
                            "The Quarto (.qmd) source and, if available, the LaTeX (.tex) "
                            "output are still provided below."
                        )
                        with st.expander("🔎 Show render error"):
                            for note in heal_notes:
                                st.markdown(f"- {note}")
                            st.code(render_error or "No error details available.")

                    if pdf_output:
                        with open(pdf_output, "rb") as f:
                            pdf_bytes = f.read()
                        # Adobe Auto-Tag DISABLED (requires paid account).
                        # Re-enable by restoring the sidebar checkbox block
                        # and uncommenting the call below.
                        # if enable_autotag:
                        #     with st.spinner("🏷️ Applying Adobe Auto-Tag for PDF/UA accessibility..."):
                        #         try:
                        #             pdf_bytes = autotag_pdf_with_adobe(pdf_bytes)
                        #         except Exception as e:
                        #             logger.error(f"Adobe Auto-Tag failed unexpectedly: {e}", exc_info=True)
                        #             st.warning(f"⚠️ Adobe Auto-Tag failed: {e}. PDF saved without accessibility tags.")
                        st.session_state.pdf_data = pdf_bytes

                col1, col2, col3, col4 = st.columns(4)

                # Download .qmd (reflects the healed source if self-healing ran)
                with col1:
                    st.download_button(
                        label="📄 Quarto (.qmd)",
                        data=st.session_state.qmd_content,
                        file_name=f"{original_filename}.qmd",
                        mime="text/plain",
                        help="Download the Quarto markdown source file",
                        key="download_qmd"
                    )

                # Download PDF (rendered above)
                with col2:
                    if 'pdf_data' in st.session_state:
                        st.download_button(
                            label="📕 PDF Document",
                            data=st.session_state.pdf_data,
                            file_name=f"{original_filename}.pdf",
                            mime="application/pdf",
                            help="Download as accessible PDF with proper structure and metadata",
                            key="download_pdf"
                        )
                    else:
                        st.warning("⚠️ PDF unavailable - rendering failed")

                # Render and download Word
                with col3:
                    if 'docx_data' not in st.session_state:
                        with st.spinner("🔄 Rendering Word document..."):
                            docx_output = render_quarto(st.session_state.qmd_path, "docx", st.session_state.temp_dir_path)
                            if docx_output:
                                with open(docx_output, "rb") as f:
                                    st.session_state.docx_data = f.read()

                    if 'docx_data' in st.session_state:
                        st.download_button(
                            label="📘 Word (.docx)",
                            data=st.session_state.docx_data,
                            file_name=f"{original_filename}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            help="Download as Microsoft Word document with accessibility features",
                            key="download_docx"
                        )
                    else:
                        st.warning("⚠️ Word unavailable - Quarto installation required")

                # Render and download LaTeX
                with col4:
                    if 'tex_data' not in st.session_state:
                        with st.spinner("🔄 Rendering LaTeX source..."):
                            tex_output = render_quarto(st.session_state.qmd_path, "latex", st.session_state.temp_dir_path)
                            # Salvage: if the dedicated LaTeX render failed, fall back
                            # to any .tex the PDF pipeline left behind (keep-tex: true),
                            # so a failed self-heal still yields a usable .tex.
                            if not tex_output:
                                tex_candidates = list(Path(st.session_state.temp_dir_path).glob("*.tex"))
                                if tex_candidates:
                                    tex_output = max(tex_candidates, key=lambda f: f.stat().st_mtime)
                            if tex_output:
                                with open(tex_output, "r", encoding="utf-8") as f:
                                    st.session_state.tex_data = f.read()

                    if 'tex_data' in st.session_state:
                        st.download_button(
                            label="📗 LaTeX (.tex)",
                            data=st.session_state.tex_data,
                            file_name=f"{original_filename}.tex",
                            mime="text/plain",
                            help="Download as LaTeX source file for further customization",
                            key="download_tex"
                        )
                    else:
                        st.warning("⚠️ LaTeX unavailable - Quarto installation required")

        except Exception as e:
            st.error(f"❌ An error occurred during processing: {str(e)}")
            st.info("💡 Please check your file and try again. If the problem persists, ensure Quarto is installed correctly.")
            # Cleanup on error: remove the temp dir AND clear its tracking keys so
            # a fresh dir is created on the next attempt (avoids pointing at a
            # deleted directory on rerun).
            old_dir = st.session_state.pop('temp_dir_path', None)
            st.session_state.pop('active_filename', None)
            if old_dir:
                shutil.rmtree(old_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
