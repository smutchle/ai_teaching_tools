"""
WCAG 2.1 AA Accessibility Converter

A Streamlit application that converts documents to WCAG 2.1 Level AA accessible forms.
Supports: HTML, Markdown, LaTeX, PDF, PowerPoint, and Quarto Markdown files.
"""

import io
import os
import sys
import zipfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from processors import (
    HTMLProcessor,
    MarkdownProcessor,
    QMDProcessor,
    LaTeXProcessor,
    PDFProcessor,
    AdobeAutoTagPDFProcessor,
    PowerPointProcessor,
)
from utils.claude_client import ClaudeClient
from utils.accessibility import AccessibilityReport

# Load environment variables
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="WCAG Accessibility Converter",
    layout="wide",
)

# Supported file types and their processors
# Note: PDFs use Adobe Auto-Tag API by default for production-grade accessibility
# Falls back to basic PDFProcessor if Adobe credentials/SDK not available
FILE_PROCESSORS = {
    '.html': HTMLProcessor,
    '.htm': HTMLProcessor,
    '.md': MarkdownProcessor,
    '.markdown': MarkdownProcessor,
    '.tex': LaTeXProcessor,
    '.pdf': AdobeAutoTagPDFProcessor if AdobeAutoTagPDFProcessor is not None else PDFProcessor,
    '.pptx': PowerPointProcessor,
    '.qmd': QMDProcessor,
}

ALLOWED_EXTENSIONS = list(FILE_PROCESSORS.keys())


def get_processor_for_file(filename: str, claude_client: ClaudeClient):
    """Get the appropriate processor for a file based on extension."""
    ext = Path(filename).suffix.lower()
    processor_class = FILE_PROCESSORS.get(ext)

    if processor_class:
        # Special handling for PDF processor - use Adobe Auto-Tag if credentials available
        if AdobeAutoTagPDFProcessor is not None and processor_class == AdobeAutoTagPDFProcessor:
            try:
                return AdobeAutoTagPDFProcessor(claude_client)
            except EnvironmentError as e:
                # Adobe credentials not configured, fall back to basic PDF processor
                st.warning(
                    "⚠️ Adobe PDF Services credentials not configured. "
                    "Using basic PDF processor. For full WCAG compliance with auto-tagging, "
                    "configure Adobe PDF Services API credentials."
                )
                return PDFProcessor(claude_client)
            except Exception as e:
                st.warning(
                    f"⚠️ Adobe Auto-Tag processor failed to initialize: {e}. "
                    "Using basic PDF processor."
                )
                return PDFProcessor(claude_client)

        return processor_class(claude_client)
    return None


def process_file(uploaded_file, claude_client: ClaudeClient) -> tuple[bytes, AccessibilityReport, str]:
    """
    Process a single file for accessibility.

    Returns:
        Tuple of (processed_content, report, error_message)
    """
    filename = uploaded_file.name
    content = uploaded_file.read()

    processor = get_processor_for_file(filename, claude_client)
    if not processor:
        return None, None, f"Unsupported file type: {Path(filename).suffix}"

    try:
        processed_content = processor.process(content, filename)
        report = processor.get_report()
        return processed_content, report, None
    except Exception as e:
        return None, None, f"Error processing {filename}: {str(e)}"


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
    st.title("WCAG 2.1 AA Accessibility Converter")

    st.markdown("""
    Convert your documents to WCAG 2.1 Level AA accessible forms.

    **Supported formats:** HTML, Markdown (.md), Quarto (.qmd), LaTeX (.tex), PDF, PowerPoint (.pptx)

    **Features:**
    - AI-generated alt text for images
    - Heading hierarchy fixes
    - Link text improvements
    - Table accessibility enhancements
    - Document structure validation
    - And more...
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
        help="Upload one or more documents to convert to accessible forms"
    )

    if not uploaded_files:
        st.info("👆 Upload one or more files to get started")
        return

    # Display uploaded files
    st.markdown(f"**{len(uploaded_files)} file(s) uploaded:**")
    for f in uploaded_files:
        st.markdown(f"- {f.name} ({f.size:,} bytes)")

    # Process button
    if st.button("🔄 Convert to Accessible Format", type="primary"):
        processed_files = []
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
            processed_content, report, error = process_file(uploaded_file, claude_client)

            if error:
                errors.append(error)
            else:
                # Add "accessible_" prefix to filename
                new_filename = f"accessible_{uploaded_file.name}"
                processed_files.append((new_filename, processed_content))
                reports.append((uploaded_file.name, report))

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

            if len(processed_files) == 1:
                # Single file download
                filename, content = processed_files[0]
                st.download_button(
                    label=f"Download {filename}",
                    data=content,
                    file_name=filename,
                    mime="application/octet-stream"
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
    """)


if __name__ == "__main__":
    main()
