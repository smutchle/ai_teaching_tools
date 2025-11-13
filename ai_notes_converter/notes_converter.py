import streamlit as st
import os
import base64
import tempfile
import shutil
import uuid
from pathlib import Path
from pdf2image import convert_from_path
from anthropic import Anthropic
from dotenv import load_dotenv
import subprocess

# Load environment variables
load_dotenv()

# Initialize Anthropic client
client = Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

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

def extract_text_from_page(image, page_num, temp_dir):
    """Use Claude vision API to extract handwritten text from a page image.

    Args:
        image: PIL Image object
        page_num: Page number
        temp_dir: Temporary directory to save image files

    Returns:
        Tuple of (extracted_text, image_references)
        where image_references is a list of (image_filename, description) tuples
    """
    image_b64 = image_to_base64(image)

    prompt = f"""You are analyzing page {page_num} of handwritten notes. Please extract ALL text, equations, and content from this page.

IMPORTANT INSTRUCTIONS:
- Preserve the structure and organization of the content
- Convert ALL mathematical expressions and equations to LaTeX format (use $...$ for inline math and $$...$$ for display math)
- Identify and preserve any headings, lists, or structured content
- Be thorough and capture all visible text and formulas
- If there are diagrams, drawings, graphs, or figures, mark their location with {{{{FIGURE: brief description}}}} and I will preserve them as images
- Do NOT attempt to describe complex diagrams in detail - just note their presence and general purpose

Please provide the extracted content in a clean, readable format."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
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

    extracted_text = message.content[0].text

    # Check if there are any figure markers indicating we should preserve the image
    has_figures = "{{FIGURE:" in extracted_text or "[FIGURE:" in extracted_text or "diagram" in extracted_text.lower() or "drawing" in extracted_text.lower()

    image_references = []
    if has_figures:
        # Save the page image for inclusion in the document
        image_filename = f"page_{page_num}.png"
        image_path = Path(temp_dir) / image_filename
        image.save(image_path, format="PNG")
        image_references.append((image_filename, f"Page {page_num} containing diagrams/drawings"))

    return extracted_text, image_references

def create_quarto_document(pages_content, make_accessible=False):
    """Create a Quarto .qmd document from extracted pages.

    Args:
        pages_content: List of tuples (text_content, image_references)
        make_accessible: Whether to add accessibility features
    """
    # YAML frontmatter with accessibility options
    yaml_header = """---
title: "Converted Handwritten Notes"
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
        yaml_header += """    include-in-header:
      text: |
        \\usepackage[utf8]{inputenc}
        \\usepackage{fontspec}
        \\usepackage{hyperref}
        \\hypersetup{pdfauthor={Converted from PDF}, pdftitle={Converted Handwritten Notes}, pdfsubject={Handwritten Notes}, pdfkeywords={notes, handwritten}, colorlinks=true, bookmarks=true, bookmarksopen=true, bookmarksnumbered=true}
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
        if i > 1 and make_accessible:
            document += f"{{{{< pagebreak >}}}}\n\n"

        if make_accessible:
            # Add accessibility markers
            document += f"::: {{.page-content aria-label=\"Content from page {i}\"}}\n\n"

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

def main():
    st.set_page_config(page_title="Note Converter", page_icon="📝", layout="wide")

    st.title("📝 Note Converter")
    st.markdown("Upload your handwritten PDF notes to convert them to Quarto, LaTeX, Word and PDF documents with accessibility features for screen readers.")

    # Check Quarto installation (silently - only warn if missing)
    quarto_ok, quarto_info = check_quarto_installation()
    if not quarto_ok:
        st.error(f"❌ Quarto issue: {quarto_info}")
        st.warning("PDF/Word/LaTeX downloads may not work without Quarto installed.")

    # Sidebar options
    with st.sidebar:
        st.header("Options")
        make_accessible = st.checkbox("Make Accessible",
                                     value=True,
                                     help="Add accessibility features for screen readers")

        st.markdown("---")
        st.markdown("### About")
        st.markdown("""
        This app uses Claude Sonnet's vision capabilities to:
        - Extract handwritten text from PDF pages
        - Convert mathematical notation to LaTeX format
        - Preserve diagrams and drawings as embedded images
        - Generate multiple output formats (PDF, Word, LaTeX)
        """)

    # File upload
    uploaded_file = st.file_uploader("Upload PDF file", type=["pdf"])

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

            st.success(f"✓ Uploaded: {uploaded_file.name}")

            # Process button
            if st.button("🔄 Convert", type="primary"):
                with st.spinner("Converting PDF pages to images..."):
                    images = pdf_to_images(str(pdf_path))
                    st.info(f"Processing {len(images)} pages...")

                # Extract text from each page
                pages_content = []
                progress_bar = st.progress(0)

                for i, image in enumerate(images):
                    with st.spinner(f"Extracting text from page {i+1}/{len(images)}..."):
                        text_content, image_refs = extract_text_from_page(image, i+1, temp_dir_path)
                        pages_content.append((text_content, image_refs))
                        progress_bar.progress((i + 1) / len(images))

                st.success(f"✓ Extracted text from {len(images)} pages!")

                # Create Quarto document
                with st.spinner("Creating Quarto document..."):
                    qmd_content = create_quarto_document(pages_content, make_accessible)
                    qmd_path = temp_dir_path / f"{original_filename}.qmd"
                    with open(qmd_path, "w", encoding="utf-8") as f:
                        f.write(qmd_content)

                # Store in session state to persist across reruns
                st.session_state.qmd_content = qmd_content
                st.session_state.qmd_path = str(qmd_path)
                st.session_state.temp_dir_path = str(temp_dir_path)
                st.session_state.original_filename = original_filename
                st.session_state.conversion_complete = True

                st.success("✓ Quarto document created!")

            # Show download options if conversion is complete
            if st.session_state.get('conversion_complete', False) and st.session_state.get('original_filename') == original_filename:
                # Display preview in expander
                with st.expander("📄 Preview Quarto Content", expanded=False):
                    st.code(st.session_state.qmd_content, language="markdown")

                # Download section
                st.markdown("---")
                st.subheader("📥 Download Options")

                col1, col2, col3, col4 = st.columns(4)

                # Download .qmd
                with col1:
                    st.download_button(
                        label="Download .qmd",
                        data=st.session_state.qmd_content,
                        file_name=f"{original_filename}.qmd",
                        mime="text/plain",
                        key="download_qmd"
                    )

                # Render and download PDF
                with col2:
                    if 'pdf_data' not in st.session_state:
                        with st.spinner("Rendering PDF..."):
                            pdf_output = render_quarto(st.session_state.qmd_path, "pdf", st.session_state.temp_dir_path)
                            if pdf_output:
                                with open(pdf_output, "rb") as f:
                                    st.session_state.pdf_data = f.read()
                            else:
                                st.error("❌ Failed to render PDF from Quarto")

                    if 'pdf_data' in st.session_state:
                        st.download_button(
                            label="Download PDF",
                            data=st.session_state.pdf_data,
                            file_name=f"{original_filename}.pdf",
                            mime="application/pdf",
                            key="download_pdf"
                        )
                    else:
                        st.warning("PDF rendering failed - check Quarto installation")

                # Render and download Word
                with col3:
                    if 'docx_data' not in st.session_state:
                        with st.spinner("Rendering Word..."):
                            docx_output = render_quarto(st.session_state.qmd_path, "docx", st.session_state.temp_dir_path)
                            if docx_output:
                                with open(docx_output, "rb") as f:
                                    st.session_state.docx_data = f.read()

                    if 'docx_data' in st.session_state:
                        st.download_button(
                            label="Download Word",
                            data=st.session_state.docx_data,
                            file_name=f"{original_filename}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key="download_docx"
                        )

                # Render and download LaTeX
                with col4:
                    if 'tex_data' not in st.session_state:
                        with st.spinner("Rendering LaTeX..."):
                            tex_output = render_quarto(st.session_state.qmd_path, "latex", st.session_state.temp_dir_path)
                            if tex_output:
                                with open(tex_output, "r", encoding="utf-8") as f:
                                    st.session_state.tex_data = f.read()

                    if 'tex_data' in st.session_state:
                        st.download_button(
                            label="Download LaTeX",
                            data=st.session_state.tex_data,
                            file_name=f"{original_filename}.tex",
                            mime="text/plain",
                            key="download_tex"
                        )

        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
            # Cleanup on error
            try:
                shutil.rmtree(session_temp_dir, ignore_errors=True)
            except Exception:
                pass

if __name__ == "__main__":
    main()
