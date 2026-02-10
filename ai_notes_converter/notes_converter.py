import streamlit as st
import os
import base64
import tempfile
import shutil
import uuid
import logging
from pathlib import Path
from pdf2image import convert_from_path
from anthropic import Anthropic
from anthropic.types import TextBlock
from dotenv import load_dotenv
import subprocess

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Anthropic client
client = Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# Get model from environment variable with fallback
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

# Initialize session state
if 'session_id' not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

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
- Keep all {{{{FIGURE:...}}}} markers exactly as they are

Here is the extracted text to review:

{extracted_text}

Please return the corrected text with ONLY obvious OCR errors fixed. Do not add any page numbers or headers."""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    content_block = message.content[0]
    if isinstance(content_block, TextBlock):
        return content_block.text
    else:
        raise ValueError(f"Expected TextBlock but got {type(content_block)}")


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
- Identify and preserve any headings, lists, or structured content
- Be thorough and capture all visible text and formulas
- Do NOT add page numbers, headers, or any metadata not present in the image
{figure_instruction}

Please provide the extracted content in a clean, readable format without adding page numbers or headers."""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
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
            }
        ],
    )

    content_block = message.content[0]
    if isinstance(content_block, TextBlock):
        extracted_text = content_block.text
    else:
        raise ValueError(f"Expected TextBlock but got {type(content_block)}")

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
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        )

        content_block = message.content[0]
        if isinstance(content_block, TextBlock):
            title = content_block.text.strip()
            # Remove quotes if present
            title = title.strip('"').strip("'")
            return title
        else:
            return "Converted Handwritten Notes"
    except Exception:
        return "Converted Handwritten Notes"

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

        # Add the text content
        document += content + "\n\n"

        if make_accessible:
            document += ":::\n\n"

    return document

def render_quarto(qmd_path, output_format, work_dir):
    """Render Quarto document to specified format."""
    try:
        if output_format == "pdf":
            cmd = ["quarto", "render", qmd_path, "--to", "pdf"]
        elif output_format == "docx":
            cmd = ["quarto", "render", qmd_path, "--to", "docx"]
        elif output_format == "latex":
            cmd = ["quarto", "render", qmd_path, "--to", "latex"]
        else:
            return None

        result = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)

        if result.returncode != 0:
            st.error(f"Quarto rendering error (exit code {result.returncode}):")
            st.error(f"STDERR: {result.stderr}")
            if result.stdout:
                st.error(f"STDOUT: {result.stdout}")
            return None

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
            return None

        # Find the output file by checking modification time
        # Quarto creates/overwrites files after the .qmd file
        qmd_mtime = Path(qmd_path).stat().st_mtime
        output_file = work_dir_path / f"{qmd_stem}{extension}"

        if output_file.exists():
            file_mtime = output_file.stat().st_mtime
            # If the output file was modified after (or at the same time as) the qmd file, it's the rendered version
            if file_mtime >= qmd_mtime:
                return output_file

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
            return output_file
        else:
            st.error(f"Output file not found in {work_dir_path}")
            st.error(f"Looking for files with extension: {extension}")
            all_files = list(work_dir_path.glob("*"))
            st.error(f"Files in directory: {[f.name for f in all_files]}")
            return None

    except Exception as e:
        st.error(f"Error rendering Quarto document: {str(e)}")
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
        return stream_asset.get_input_stream()

    except (ServiceApiException, ServiceUsageException) as e:
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

    st.title("📝 Note Converter")
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

        # Adobe Auto-Tag option
        adobe_available = check_adobe_credentials()
        enable_autotag = st.checkbox(
            "Adobe PDF Auto-Tag",
            value=adobe_available,
            disabled=not adobe_available,
            help="Uses Adobe PDF Services API to add production-grade accessibility tags (PDF/UA) to the rendered PDF. "
                 "Requires PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET in .env file."
        )
        if not adobe_available:
            st.caption("Adobe credentials not configured. Set PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET in .env")

        st.markdown("---")
        st.markdown("### About This Tool")
        st.markdown("""
        This application uses Claude Sonnet 4.5's advanced vision AI to:
        - **Extract** handwritten text from PDF pages with high accuracy
        - **Convert** mathematical notation to accessible LaTeX format
        - **Preserve** diagrams and drawings as embedded images with alt text
        - **Generate** multiple accessible output formats (PDF, Word, LaTeX, Quarto)

        **Accessibility Commitment:**
        Output documents comply with WCAG 2.1 Level AA standards when "Enable Accessibility Features" is checked.
        """)

    # File upload
    st.markdown("### Step 1: Upload Your Document")
    uploaded_file = st.file_uploader(
        "Select a PDF file containing handwritten notes to convert",
        type=["pdf"],
        help="Upload a PDF file with handwritten notes. The file will be processed page-by-page to extract text, equations, and figures."
    )

    if uploaded_file is not None:
        # Extract original filename without extension
        original_filename = Path(uploaded_file.name).stem

        # Clean up old session data if switching to a new file
        if st.session_state.get('original_filename') != original_filename:
            # Clear previous file's data
            for key in ['qmd_content', 'qmd_path', 'temp_dir_path', 'pdf_data', 'docx_data', 'tex_data', 'conversion_complete']:
                if key in st.session_state:
                    del st.session_state[key]
            # Clean up old temp directory
            if 'temp_dir_path' in st.session_state:
                try:
                    shutil.rmtree(st.session_state.temp_dir_path, ignore_errors=True)
                except Exception:
                    pass

        # Create session-specific temporary directory for processing
        session_temp_dir = tempfile.mkdtemp(prefix=f"noteconv_{st.session_state.session_id[:8]}_")
        temp_dir_path = Path(session_temp_dir)

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

                # Store in session state to persist across reruns
                st.session_state.qmd_content = qmd_content
                st.session_state.qmd_path = str(qmd_path)
                st.session_state.temp_dir_path = str(temp_dir_path)
                st.session_state.original_filename = original_filename
                st.session_state.conversion_complete = True

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

                col1, col2, col3, col4 = st.columns(4)

                # Download .qmd
                with col1:
                    st.download_button(
                        label="📄 Quarto (.qmd)",
                        data=st.session_state.qmd_content,
                        file_name=f"{original_filename}.qmd",
                        mime="text/plain",
                        help="Download the Quarto markdown source file",
                        key="download_qmd"
                    )

                # Render and download PDF
                with col2:
                    if 'pdf_data' not in st.session_state:
                        with st.spinner("🔄 Rendering PDF document..."):
                            pdf_output = render_quarto(st.session_state.qmd_path, "pdf", st.session_state.temp_dir_path)
                            if pdf_output:
                                with open(pdf_output, "rb") as f:
                                    pdf_bytes = f.read()
                                if enable_autotag:
                                    with st.spinner("🏷️ Applying Adobe Auto-Tag for PDF/UA accessibility..."):
                                        pdf_bytes = autotag_pdf_with_adobe(pdf_bytes)
                                st.session_state.pdf_data = pdf_bytes
                            else:
                                st.error("❌ PDF rendering failed. Ensure Quarto is installed correctly.")

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
                        st.warning("⚠️ PDF unavailable - Quarto installation required")

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
            # Cleanup on error
            try:
                shutil.rmtree(session_temp_dir, ignore_errors=True)
            except Exception:
                pass

if __name__ == "__main__":
    main()
