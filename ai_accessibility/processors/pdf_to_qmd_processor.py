"""PDF to Quarto Markdown converter processor."""

import io
import os
import re
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional
import fitz  # PyMuPDF
from .base import BaseProcessor
from utils.accessibility import AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


class PDFToQMDProcessor(BaseProcessor):
    """
    Processor that converts PDF documents to Quarto Markdown format,
    then renders back to accessible PDF using Quarto.

    This approach:
    1. Extracts content from PDF and converts to QMD
    2. Renders QMD to PDF using Quarto (which creates properly tagged PDFs)
    3. Returns the accessible PDF

    The intermediate QMD is also available via get_qmd_content().
    """

    def __init__(self, claude_client: Optional[ClaudeClient] = None, render_to_pdf: bool = True):
        super().__init__(claude_client)
        self.images_dir = "images"
        self.render_to_pdf = render_to_pdf
        self.qmd_content = None  # Store QMD content for reference

    def get_file_extension(self) -> str:
        return ".pdf"

    def get_qmd_content(self) -> Optional[str]:
        """Get the intermediate QMD content."""
        return self.qmd_content

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Convert PDF to Quarto Markdown, then optionally render to PDF.

        Args:
            content: PDF content as bytes
            filename: Original filename

        Returns:
            PDF bytes (if render_to_pdf=True) or QMD bytes (if render_to_pdf=False)
        """
        self.reset_report()

        # Open PDF
        doc = fitz.open(stream=content, filetype="pdf")

        try:
            # Extract structure and content
            metadata = doc.metadata
            title = metadata.get('title', '') or Path(filename).stem or "Document"
            author = metadata.get('author', 'Unknown')

            # Build QMD content
            qmd_lines = []

            # Add YAML frontmatter
            qmd_lines.extend([
                "---",
                f"title: \"{title}\"",
                f"author: \"{author}\"",
                "lang: en",
                "format:",
                "  html:",
                "    toc: true",
                "    toc-title: \"Table of Contents\"",
                "    code-fold: true",
                "  pdf:",
                "    toc: true",
                "---",
                ""
            ])

            self.report.add_fix("Created QMD frontmatter with accessibility options")

            # Extract content page by page
            image_counter = 0

            for page_num in range(len(doc)):
                page = doc[page_num]

                # Extract text blocks with structure hints
                blocks = page.get_text("dict")["blocks"]

                for block in blocks:
                    if block.get("type") == 0:  # Text block
                        text_content = self._extract_text_from_block(block)
                        if text_content:
                            qmd_lines.append(text_content)

                # Extract images
                image_list = page.get_images(full=True)

                for img_index, img_info in enumerate(image_list):
                    xref = img_info[0]

                    try:
                        # Extract image
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]

                        # Generate filename
                        image_filename = f"{self.images_dir}/image_{page_num + 1}_{img_index + 1}.{image_ext}"

                        # Get surrounding text for context
                        page_text = page.get_text()[:500]

                        # Generate alt text using Claude
                        try:
                            media_type_map = {
                                'png': 'image/png',
                                'jpg': 'image/jpeg',
                                'jpeg': 'image/jpeg',
                                'gif': 'image/gif',
                                'bmp': 'image/bmp',
                            }
                            media_type = media_type_map.get(image_ext.lower(), 'image/png')

                            alt_result = self.claude_client.describe_complex_image(
                                image_data=image_bytes,
                                media_type=media_type,
                                context=f"PDF page {page_num + 1}. Surrounding text: {page_text}"
                            )

                            alt_text = alt_result.get('alt_text', 'Image description')
                            is_complex = alt_result.get('is_complex', False)
                            long_desc = alt_result.get('long_description', '')

                            # Add image to QMD with Quarto figure syntax
                            qmd_lines.append("")
                            if is_complex and long_desc:
                                # Complex image with long description
                                qmd_lines.append(f"![{long_desc[:100]}...]({image_filename}){{fig-alt=\"{alt_text}\"}}")
                                qmd_lines.append("")
                                qmd_lines.append(f"::: {{.callout-note collapse=\"true\" title=\"Detailed Description\"}}")
                                qmd_lines.append(long_desc)
                                qmd_lines.append(":::")
                                self.report.add_fix(f"Added complex image with long description: {image_filename}")
                            else:
                                # Simple image
                                qmd_lines.append(f"![]({image_filename}){{fig-alt=\"{alt_text}\"}}")
                                self.report.add_fix(f"Added image with alt text: {image_filename}")

                            qmd_lines.append("")

                            # Store image data (would need to be saved separately in real implementation)
                            image_counter += 1

                        except Exception as e:
                            # Fallback without alt text
                            qmd_lines.append(f"![]({image_filename}){{fig-alt=\"Image from page {page_num + 1}\"}}")
                            self.report.add_warning(f"Could not generate alt text for image: {str(e)}")

                    except Exception as e:
                        self.report.add_warning(f"Could not extract image from page {page_num + 1}: {str(e)}")

                # Add page break comment
                if page_num < len(doc) - 1:
                    qmd_lines.append("")
                    qmd_lines.append(f"<!-- Page {page_num + 2} -->")
                    qmd_lines.append("")

            # Apply additional QMD accessibility fixes
            qmd_content = "\n".join(qmd_lines)
            qmd_content = self._fix_heading_hierarchy(qmd_content)

            # Store QMD content
            self.qmd_content = qmd_content

            # Add informational notes
            self.report.add_fix(
                f"Converted PDF to Quarto Markdown format. Extracted {image_counter} images."
            )

            # Note about limitations
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description="PDF→QMD conversion may lose some structure (tables, multi-column layouts)",
                suggestion="Review converted content and manually fix table structures if needed"
            ))

            # Render to PDF using Quarto if requested
            if self.render_to_pdf:
                try:
                    pdf_bytes = self._render_qmd_to_pdf(qmd_content, filename)
                    self.report.add_fix("Rendered QMD to accessible PDF using Quarto")
                    self.report.add_warning(
                        "Note: Images are referenced in QMD but not embedded. "
                        "For full rendering with images, save QMD and images separately and render with Quarto."
                    )
                    return pdf_bytes
                except Exception as e:
                    self.report.add_warning(
                        f"Could not render QMD to PDF: {str(e)}. "
                        "Returning QMD content instead. Ensure Quarto is installed: https://quarto.org/docs/get-started/"
                    )
                    return qmd_content.encode('utf-8')
            else:
                self.report.add_warning(
                    "QMD can be rendered to accessible PDF using 'quarto render file.qmd --to pdf'. "
                    "Images are referenced but not embedded - save them separately to the 'images/' directory."
                )
                return qmd_content.encode('utf-8')

        finally:
            doc.close()

    def _extract_text_from_block(self, block: dict) -> str:
        """
        Extract text from a block and determine its likely semantic role.

        Returns formatted markdown text.
        """
        lines = block.get("lines", [])
        if not lines:
            return ""

        # Analyze first line to determine if this is a heading
        first_line = lines[0]
        spans = first_line.get("spans", [])

        if not spans:
            return ""

        # Get text and font info
        text_parts = []
        max_font_size = 0
        is_bold = False

        for span in spans:
            text_parts.append(span.get("text", ""))
            max_font_size = max(max_font_size, span.get("size", 12))
            is_bold = is_bold or bool(span.get("flags", 0) & 2**4)

        text = "".join(text_parts).strip()

        if not text:
            return ""

        # Determine heading level based on font size
        if max_font_size >= 24:
            return f"# {text}\n"
        elif max_font_size >= 20:
            return f"## {text}\n"
        elif max_font_size >= 16:
            return f"### {text}\n"
        elif max_font_size >= 14 and is_bold:
            return f"#### {text}\n"
        else:
            # Regular paragraph text
            # Collect all lines in the block
            block_text = []
            for line in lines:
                line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                if line_text.strip():
                    block_text.append(line_text.strip())

            if block_text:
                return " ".join(block_text) + "\n\n"
            return ""

    def _fix_heading_hierarchy(self, content: str) -> str:
        """
        Fix heading hierarchy to ensure no levels are skipped.
        """
        lines = content.split('\n')

        # Find all headings
        headings = []
        for i, line in enumerate(lines):
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                if level > 0 and level <= 6:
                    headings.append((i, level))

        if not headings:
            return content

        # Check and fix hierarchy
        prev_level = 0
        fixes_made = False

        for line_idx, level in headings:
            if prev_level > 0 and level > prev_level + 1:
                # Skip detected - fix it
                new_level = prev_level + 1
                old_line = lines[line_idx]
                heading_text = old_line.lstrip('#').strip()
                lines[line_idx] = '#' * new_level + ' ' + heading_text
                fixes_made = True
                self.report.add_fix(f"Fixed heading level skip: h{level} → h{new_level}")
                prev_level = new_level
            else:
                prev_level = level

        if fixes_made:
            return '\n'.join(lines)

        return content

    def _render_qmd_to_pdf(self, qmd_content: str, original_filename: str) -> bytes:
        """
        Render QMD content to PDF using Quarto.

        Args:
            qmd_content: The QMD content as string
            original_filename: Original PDF filename for naming

        Returns:
            PDF bytes

        Raises:
            Exception: If Quarto is not installed or rendering fails
        """
        # Check if Quarto is installed
        try:
            result = subprocess.run(
                ['quarto', '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise Exception("Quarto command failed")
        except FileNotFoundError:
            raise Exception(
                "Quarto not found. Install from https://quarto.org/docs/get-started/"
            )
        except subprocess.TimeoutExpired:
            raise Exception("Quarto command timed out")

        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='qmd_render_')

        try:
            # Create images directory
            images_dir = Path(temp_dir) / "images"
            images_dir.mkdir(exist_ok=True)

            # Write QMD file
            qmd_file = Path(temp_dir) / "document.qmd"
            with open(qmd_file, 'w', encoding='utf-8') as f:
                f.write(qmd_content)

            # Render to PDF using Quarto
            # Use --to pdf for direct PDF output
            result = subprocess.run(
                ['quarto', 'render', str(qmd_file), '--to', 'pdf'],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout for rendering
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                raise Exception(f"Quarto render failed: {error_msg[:500]}")

            # Read the generated PDF
            pdf_file = Path(temp_dir) / "document.pdf"
            if not pdf_file.exists():
                raise Exception("Quarto did not produce expected PDF output")

            with open(pdf_file, 'rb') as f:
                pdf_bytes = f.read()

            return pdf_bytes

        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass  # Ignore cleanup errors
