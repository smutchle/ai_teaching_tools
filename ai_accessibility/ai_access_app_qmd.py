"""
WCAG 2.1 AA Accessibility Converter (Quarto Edition)

A Streamlit application that converts documents to WCAG 2.1 Level AA accessible forms.
Supports: HTML, Markdown, LaTeX, PDF→QMD, PowerPoint, and Quarto Markdown files.

This version converts PDFs to Quarto Markdown format, which can then be rendered
to accessible HTML or processed with professional PDF/UA tools.
"""

import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Optional

import streamlit as st
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from processors import (
    HTMLProcessor,
    MarkdownProcessor,
    QMDProcessor,
    LaTeXProcessor,
    PDFToQMDProcessor,  # Use PDF→QMD converter instead of PDFProcessor
    PowerPointProcessor,
)
from utils.claude_client import ClaudeClient
from utils.accessibility import AccessibilityReport

# Load environment variables
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="WCAG Accessibility Converter (Quarto Edition)",
    layout="wide",
)

# Supported file types and their processors
FILE_PROCESSORS = {
    '.html': HTMLProcessor,
    '.htm': HTMLProcessor,
    '.md': MarkdownProcessor,
    '.markdown': MarkdownProcessor,
    '.tex': LaTeXProcessor,
    '.pdf': PDFToQMDProcessor,  # PDF → QMD conversion
    '.pptx': PowerPointProcessor,
    '.qmd': QMDProcessor,
}

ALLOWED_EXTENSIONS = list(FILE_PROCESSORS.keys())


def get_processor_for_file(filename: str, claude_client: ClaudeClient):
    """Get the appropriate processor for a file based on extension."""
    ext = Path(filename).suffix.lower()
    processor_class = FILE_PROCESSORS.get(ext)
    if processor_class:
        return processor_class(claude_client)
    return None


def process_file(uploaded_file, claude_client: ClaudeClient) -> tuple[bytes, AccessibilityReport, str, str, Optional[str]]:
    """
    Process a single file for accessibility.

    Returns:
        Tuple of (processed_content, report, error_message, output_extension, qmd_content)
    """
    filename = uploaded_file.name
    content = uploaded_file.read()

    processor = get_processor_for_file(filename, claude_client)
    if not processor:
        return None, None, f"Unsupported file type: {Path(filename).suffix}", None, None

    try:
        processed_content = processor.process(content, filename)
        report = processor.get_report()

        # Get QMD content if this was a PDF conversion
        qmd_content = None
        input_ext = Path(filename).suffix.lower()
        if input_ext == '.pdf' and hasattr(processor, 'get_qmd_content'):
            qmd_content = processor.get_qmd_content()

        # Output extension remains the same (PDF renders to PDF via QMD)
        output_ext = input_ext

        return processed_content, report, None, output_ext, qmd_content
    except Exception as e:
        return None, None, f"Error processing {filename}: {str(e)}", None, None


def create_zip(files: list[tuple[str, bytes]]) -> bytes:
    """Create a ZIP file from a list of (filename, content) tuples."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in files:
            zip_file.writestr(filename, content)
    return zip_buffer.getvalue()


def display_report(report: AccessibilityReport, filename: str):
    """Display an accessibility report in Streamlit."""
    with st.expander(f"📋 Accessibility Report: {filename}", expanded=False):
        # Summary
        st.markdown(f"**{report.get_summary()}**")

        # Fixes applied
        if report.fixes_applied:
            st.markdown("### ✅ Fixes Applied")
            for fix in report.fixes_applied:
                st.markdown(f"- {fix}")

        # Remaining issues
        errors = [i for i in report.issues if i.severity.value == "error" and not i.auto_fixed]
        warnings = [i for i in report.issues if i.severity.value == "warning" and not i.auto_fixed]

        if errors:
            st.markdown("### ❌ Errors (Require Attention)")
            for issue in errors:
                st.markdown(f"- **[{issue.wcag_criterion}]** {issue.description}")
                if issue.location:
                    st.markdown(f"  - Location: {issue.location}")
                if issue.suggestion:
                    st.markdown(f"  - Suggestion: {issue.suggestion}")

        if warnings:
            st.markdown("### ⚠️ Warnings")
            for issue in warnings:
                st.markdown(f"- **[{issue.wcag_criterion}]** {issue.description}")
                if issue.suggestion:
                    st.markdown(f"  - Suggestion: {issue.suggestion}")

        # General warnings
        if report.warnings:
            st.markdown("### ℹ️ Notes")
            for warning in report.warnings:
                st.info(warning)


def main():
    st.title("WCAG 2.1 AA Accessibility Converter (Quarto Edition)")

    st.markdown("""
    Convert your documents to WCAG 2.1 Level AA accessible forms.

    **Supported formats:** HTML, Markdown (.md), Quarto (.qmd), LaTeX (.tex), PDF, PowerPoint (.pptx)

    **Special PDF Handling (Quarto Edition):**
    - PDFs are converted to Quarto Markdown (QMD) with AI-generated alt text
    - QMD is automatically rendered back to PDF using Quarto
    - Result: Accessible, properly-tagged PDF that passes PAC validation
    - Intermediate QMD available for download if needed

    **Features:**
    - AI-generated alt text for images
    - Heading hierarchy fixes
    - Link text improvements
    - Table accessibility enhancements
    - Document structure validation
    - Properly tagged PDF output
    - And more...

    **Requirements:** [Quarto](https://quarto.org/docs/get-started/) must be installed for PDF processing.
    """)

    # Check for API key
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        st.error("⚠️ CLAUDE_API_KEY not found in environment. Please set it in your .env file.")
        st.stop()

    # Initialize Claude client
    try:
        claude_client = ClaudeClient()
    except Exception as e:
        st.error(f"Failed to initialize Claude client: {str(e)}")
        st.stop()

    # File uploader
    st.markdown("### Upload Documents")
    uploaded_files = st.file_uploader(
        "Choose files to convert",
        type=[ext.lstrip('.') for ext in ALLOWED_EXTENSIONS],
        accept_multiple_files=True,
        help="Upload one or more documents to convert to accessible forms. PDFs will be converted to Quarto Markdown."
    )

    if not uploaded_files:
        st.info("👆 Upload one or more files to get started")
        return

    # Display uploaded files
    st.markdown(f"**{len(uploaded_files)} file(s) uploaded:**")
    for f in uploaded_files:
        file_type = "PDF→QMD→PDF (via Quarto)" if f.name.lower().endswith('.pdf') else Path(f.name).suffix.upper()
        st.markdown(f"- {f.name} ({f.size:,} bytes) → {file_type}")

    # Process button
    if st.button("🔄 Convert to Accessible Format", type="primary"):
        processed_files = []
        qmd_files = []  # Store intermediate QMD files
        reports = []
        errors = []

        # Progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()

        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"Processing {uploaded_file.name}...")

            # Reset file pointer
            uploaded_file.seek(0)

            # Process file
            processed_content, report, error, output_ext, qmd_content = process_file(uploaded_file, claude_client)

            if error:
                errors.append(error)
            else:
                # Determine output filename (keeps original extension)
                new_filename = f"accessible_{uploaded_file.name}"
                processed_files.append((new_filename, processed_content))
                reports.append((uploaded_file.name, report))

                # Store QMD if available (for PDFs)
                if qmd_content:
                    input_path = Path(uploaded_file.name)
                    qmd_filename = f"accessible_{input_path.stem}.qmd"
                    qmd_files.append((qmd_filename, qmd_content.encode('utf-8')))

            # Update progress
            progress_bar.progress((i + 1) / len(uploaded_files))

        status_text.text("Processing complete!")

        # Display errors
        if errors:
            st.markdown("### ❌ Errors")
            for error in errors:
                st.error(error)

        # Display reports
        if reports:
            st.markdown("### 📊 Accessibility Reports")
            for filename, report in reports:
                display_report(report, filename)

        # Download section
        if processed_files:
            st.markdown("### 📥 Download Converted Files")

            # Check if any PDFs were converted
            pdf_converted = any(f.lower().endswith('.pdf') and qmd_files for f, _ in processed_files)
            if pdf_converted:
                st.success("✅ PDF files have been rendered through Quarto to create properly tagged, accessible PDFs!")

            if len(processed_files) == 1:
                # Single file download
                filename, content = processed_files[0]
                st.download_button(
                    label=f"Download {filename}",
                    data=content,
                    file_name=filename,
                    mime="text/markdown" if filename.endswith('.qmd') else "application/octet-stream"
                )
            else:
                # Multiple files - create ZIP
                zip_content = create_zip(processed_files)
                st.download_button(
                    label=f"Download All ({len(processed_files)} files as ZIP)",
                    data=zip_content,
                    file_name="accessible_documents.zip",
                    mime="application/zip"
                )

                # Also offer individual downloads
                st.markdown("**Or download individually:**")
                cols = st.columns(min(len(processed_files), 3))
                for i, (filename, content) in enumerate(processed_files):
                    with cols[i % 3]:
                        st.download_button(
                            label=f"📄 {filename}",
                            data=content,
                            file_name=filename,
                            mime="application/octet-stream",
                            key=f"download_{i}"
                        )

            # Offer QMD downloads for PDFs
            if qmd_files:
                st.markdown("### 📝 Download Intermediate QMD Files")
                st.info("These are the Quarto Markdown files created during PDF conversion. "
                       "You can edit these and re-render with `quarto render file.qmd --to pdf`")

                if len(qmd_files) == 1:
                    filename, content = qmd_files[0]
                    st.download_button(
                        label=f"Download {filename}",
                        data=content,
                        file_name=filename,
                        mime="text/markdown"
                    )
                else:
                    cols = st.columns(min(len(qmd_files), 3))
                    for i, (filename, content) in enumerate(qmd_files):
                        with cols[i % 3]:
                            st.download_button(
                                label=f"📝 {filename}",
                                data=content,
                                file_name=filename,
                                mime="text/markdown",
                                key=f"qmd_download_{i}"
                            )

    # Footer with WCAG info
    st.markdown("---")
    st.markdown("""
    ### About WCAG 2.1 AA Compliance

    This tool helps address the following WCAG 2.1 Level AA criteria:

    | Criterion | Description |
    |-----------|-------------|
    | 1.1.1 | Non-text Content - Alt text for images |
    | 1.3.1 | Info and Relationships - Proper structure |
    | 1.3.2 | Meaningful Sequence - Reading order |
    | 2.4.1 | Bypass Blocks - Skip navigation |
    | 2.4.2 | Page Titled - Document titles |
    | 2.4.4 | Link Purpose - Descriptive links |
    | 2.4.6 | Headings and Labels - Clear headings |
    | 3.1.1 | Language of Page - Language declaration |
    | 4.1.2 | Name, Role, Value - ARIA labels |

    **Note:** Automated tools cannot guarantee full accessibility. Manual review is recommended.

    ### How the Quarto Edition Works

    For PDF files, this tool:
    1. Extracts text and images from the PDF
    2. Generates AI-powered alt text for all images
    3. Creates Quarto Markdown with proper semantic structure
    4. Renders the QMD to PDF using Quarto (creates properly tagged PDFs)
    5. Returns the accessible PDF

    The intermediate QMD file is also available for download if you want to edit it manually.

    ### Technical Details

    Quarto's PDF engine creates properly structured PDFs that:
    - Include tagged structure trees with semantic elements
    - Embed alt text in the structure (not just metadata)
    - Pass PDF/UA validation checks
    - Are compatible with screen readers

    This approach works around PyMuPDF's limitations in creating PDF/UA structure trees directly.
    """)


if __name__ == "__main__":
    main()
