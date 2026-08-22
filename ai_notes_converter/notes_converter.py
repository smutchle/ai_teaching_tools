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
from datetime import datetime
from pathlib import Path
from pdf2image import convert_from_path
from anthropic import Anthropic, APIError
from dotenv import load_dotenv
import subprocess

from llm import TruncatedResponseError, create_text_message
from usage_metrics import UsageRecord, infer_department, record_conversion
from vt_departments import UNKNOWN_DEPARTMENT

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Anthropic client
client = Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# Get model from environment variable with fallback
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

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
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.session_state.uploader_key = next_uploader_key
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
    response_text = create_text_message(
        client=client,
        model=CLAUDE_MODEL,
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

    extracted_text = create_text_message(
        client=client,
        model=CLAUDE_MODEL,
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
        title = create_text_message(
            client=client,
            model=CLAUDE_MODEL,
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
        safe_title = document_title.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("_", "\\_")
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

def self_heal_qmd(qmd_content, error_text):
    """Ask the LLM to repair a Quarto document that failed to render.

    Sends the full failing .qmd plus the render error back to Claude and asks
    for a corrected version. Intended for a single repair round (the caller is
    responsible for not looping).

    Args:
        qmd_content: The full source of the .qmd document that failed to render.
        error_text: The Quarto/LaTeX error output captured from the failed render.

    Returns:
        The corrected .qmd content as a string, or None if repair failed.
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
{error_text}

=== QMD SOURCE ===
{qmd_content}
"""

    try:
        fixed = create_text_message(
            client=client,
            model=CLAUDE_MODEL,
            content=prompt,
            max_tokens=32000,
            purpose="qmd render repair",
        ).strip()
        # Strip an accidental ```/```quarto code fence wrapper if present
        if fixed.startswith("```"):
            lines = fixed.split("\n")
            lines = lines[1:]  # drop opening fence line
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            fixed = "\n".join(lines).strip()
        return fixed if fixed else None
    except (APIError, TruncatedResponseError) as e:
        logger.warning(f"LLM qmd repair failed: {e}")
        return None

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
        page_icon="📝",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    render_vt_banner()
    st.title("📝 AI Handwritten Notes Converter")
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
        This application uses Anthropic Claude advanced vision AI to:
        - **Extract** handwritten text from PDF pages with high accuracy
        - **Convert** mathematical notation to accessible LaTeX format
        - **Preserve** diagrams and drawings as embedded images with alt text
        - **Generate** multiple accessible output formats (PDF, Word, LaTeX, Quarto)

        **Accessibility Commitment:**
        Output documents comply with WCAG 2.1 Level AA standards when "Enable Accessibility Features" is checked.
        """)

        st.markdown("---")
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
                        'pdf_render_failed']:
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

            # Process button
            st.markdown("### Step 2: Start Conversion")
            if st.button("🔄 Convert to Accessible Document", type="primary", help="Begin processing the uploaded PDF file"):
                with st.spinner("Converting PDF pages to images..."):
                    images = pdf_to_images(str(pdf_path))
                    st.info(f"📄 Found {len(images)} pages to process")

                # Extract text from each page
                pages_content = []
                progress_bar = st.progress(0, text="Starting conversion...")

                for i, image in enumerate(images):
                    progress_text = f"Processing page {i+1} of {len(images)}"
                    with st.spinner(f"🔍 Extracting text from page {i+1}/{len(images)}..."):
                        text_content, image_refs = extract_text_from_page(image, i+1, temp_dir_path, preserve_figures)

                        # Apply OCR error correction if enabled
                        if enable_ocr_correction:
                            with st.spinner(f"✏️ Correcting OCR errors on page {i+1}/{len(images)}..."):
                                text_content = fix_ocr_errors(text_content, i+1)

                        pages_content.append((text_content, image_refs))
                        progress_bar.progress((i + 1) / len(images), text=f"{progress_text} - Complete")

                st.success(f"✅ Successfully extracted text from all {len(images)} pages")

                # Generate document title
                with st.spinner("🤔 Generating document title..."):
                    document_title = generate_document_title(pages_content)
                    st.info(f"📝 Document title: \"{document_title}\"")

                # Create Quarto document
                with st.spinner("📝 Creating accessible Quarto document..."):
                    qmd_content = create_quarto_document(pages_content, make_accessible, remove_page_breaks, document_title)
                    qmd_path = temp_dir_path / f"{original_filename}.qmd"
                    with open(qmd_path, "w", encoding="utf-8") as f:
                        f.write(qmd_content)

                # Store in session state to persist across reruns. Clear any
                # cached renders and the self-heal flag from a prior conversion
                # so the new document renders fresh.
                for key in ['pdf_data', 'docx_data', 'tex_data', 'pdf_render_failed']:
                    st.session_state.pop(key, None)
                st.session_state.qmd_content = qmd_content
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
                        department = infer_department(client, CLAUDE_MODEL, notes_sample)
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

                # Render the PDF (with one-round self-healing) BEFORE building the
                # download columns. A successful repair rewrites the .qmd on disk
                # and in session_state, so the .qmd/.tex/.docx outputs below all
                # reflect the healed document. If healing fails, the raw .qmd (and
                # any salvageable .tex) are still offered below.
                if 'pdf_data' not in st.session_state and not st.session_state.get('pdf_render_failed'):
                    with st.spinner("🔄 Rendering PDF document..."):
                        pdf_output, render_error = render_quarto(
                            st.session_state.qmd_path, "pdf",
                            st.session_state.temp_dir_path, return_error=True
                        )

                    # Self-healing: one round only (no loop).
                    if not pdf_output:
                        st.warning(
                            "⚠️ PDF rendering failed. Starting automated self-healing — "
                            "sending the document and the render error to the AI to repair "
                            "it, then retrying the render (one attempt)..."
                        )
                        with st.spinner("🩹 Self-healing the document and retrying the render..."):
                            fixed_qmd = self_heal_qmd(st.session_state.qmd_content, render_error)
                            if fixed_qmd:
                                # Belt-and-braces: mechanically repair known
                                # render-killers in the healed document too.
                                # (Not sanitize_body_content -- its horizontal-
                                # rule rewrite would clobber the YAML
                                # frontmatter fences of a full document.)
                                fixed_qmd = strip_figure_markers(fix_latex_math_errors(fixed_qmd))
                            if fixed_qmd == st.session_state.qmd_content:
                                st.warning(
                                    "⚠️ Self-healing returned the document unchanged — "
                                    "the repair step could not identify a fix."
                                )
                            elif fixed_qmd is None:
                                st.warning(
                                    "⚠️ Self-healing did not return a repaired document "
                                    "(the repair request failed)."
                                )
                            if fixed_qmd and fixed_qmd != st.session_state.qmd_content:
                                # Persist the repaired document and drop stale derived
                                # outputs so every format re-renders from the fix.
                                with open(st.session_state.qmd_path, "w", encoding="utf-8") as f:
                                    f.write(fixed_qmd)
                                st.session_state.qmd_content = fixed_qmd
                                for k in ['docx_data', 'tex_data']:
                                    st.session_state.pop(k, None)
                                pdf_output, render_error = render_quarto(
                                    st.session_state.qmd_path, "pdf",
                                    st.session_state.temp_dir_path, return_error=True
                                )

                        if pdf_output:
                            st.success("✅ Self-healing succeeded — the document was repaired and rendered to PDF.")
                        else:
                            st.session_state.pdf_render_failed = True
                            st.error(
                                "❌ PDF rendering still failed after one self-healing attempt. "
                                "The Quarto (.qmd) source and, if available, the LaTeX (.tex) "
                                "output are still provided below."
                            )
                            with st.expander("🔎 Show render error"):
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
