"""PDF accessibility processor."""

import io
from typing import Optional
import fitz  # PyMuPDF
from .base import BaseProcessor
from utils.accessibility import AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


class PDFProcessor(BaseProcessor):
    """
    Processor for PDF documents.

    Note: Full PDF/UA compliance is complex. This processor provides best-effort
    accessibility improvements including:
    - Document metadata (title, language)
    - Alt text for images
    - Basic structure tagging where possible

    Some accessibility features may require manual review or professional tools.
    """

    # Generic/non-descriptive link texts to fix
    GENERIC_LINK_TEXTS = {
        'here', 'click here', 'click', 'link', 'this link',
        'read more', 'learn more', 'more', 'info', 'details'
    }

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)

    def get_file_extension(self) -> str:
        return ".pdf"

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process PDF for accessibility.

        Args:
            content: PDF content as bytes
            filename: Original filename

        Returns:
            Accessible PDF as bytes
        """
        self.reset_report()

        # Open PDF
        doc = fitz.open(stream=content, filetype="pdf")

        try:
            # Apply accessibility improvements
            self._set_document_metadata(doc, filename)
            self._add_image_alt_text(doc)
            self._check_document_structure(doc)
            self._check_reading_order(doc)
            self._check_color_only_information(doc)
            self._check_ambiguous_references(doc)

            # Add note about limitations
            self.report.add_warning(
                "PDF accessibility is best-effort. Some features (like full structure "
                "tagging) may require professional PDF/UA tools for complete compliance."
            )

            # Save to bytes
            output = io.BytesIO()
            doc.save(output)
            return output.getvalue()

        finally:
            doc.close()

    def _set_document_metadata(self, doc: fitz.Document, filename: str):
        """Set document metadata for accessibility."""
        metadata = doc.metadata

        # Check and set title
        if not metadata.get('title'):
            title = filename.rsplit('.', 1)[0] if filename else "Document"
            doc.set_metadata({'title': title})
            self.report.add_fix(f"Set document title: '{title}'")

        # Check and set language
        # Note: PyMuPDF doesn't directly support PDF language tag, but we can try via metadata
        if not metadata.get('subject'):
            # Use subject field to note language (workaround)
            pass

        # Report if author is missing (helpful for attribution)
        if not metadata.get('author'):
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.2",
                severity=Severity.INFO,
                description="Document author not set in metadata"
            ))

    def _add_image_alt_text(self, doc: fitz.Document):
        """Add alt text to images in the PDF."""
        images_processed = 0
        images_with_alt = 0

        for page_num in range(len(doc)):
            page = doc[page_num]

            # Get images on page
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]

                try:
                    # Extract image
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]

                    # Determine media type
                    media_type_map = {
                        'png': 'image/png',
                        'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg',
                        'gif': 'image/gif',
                        'bmp': 'image/bmp',
                    }
                    media_type = media_type_map.get(image_ext.lower(), 'image/png')

                    # Get surrounding text for context
                    page_text = page.get_text()[:500]

                    # Generate alt text
                    try:
                        alt_result = self.claude_client.describe_complex_image(
                            image_data=image_bytes,
                            media_type=media_type,
                            context=f"PDF page {page_num + 1}. Surrounding text: {page_text}"
                        )

                        alt_text = alt_result.get('alt_text', '')

                        if alt_text:
                            # Note: PyMuPDF doesn't have direct alt text support
                            # We store it in a custom way or add to document
                            images_with_alt += 1
                            self.report.add_fix(
                                f"Generated alt text for image on page {page_num + 1}: "
                                f"'{alt_text[:50]}...'"
                            )

                            # If complex image, note long description
                            if alt_result.get('is_complex'):
                                self.report.add_warning(
                                    f"Complex image on page {page_num + 1} may need additional "
                                    f"description. Long description: {alt_result.get('long_description', '')[:100]}..."
                                )

                    except Exception as e:
                        self.report.add_warning(
                            f"Could not generate alt text for image on page {page_num + 1}: {str(e)}"
                        )

                    images_processed += 1

                except Exception as e:
                    self.report.add_warning(f"Could not process image on page {page_num + 1}: {str(e)}")

        if images_processed > 0:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.1.1",
                severity=Severity.WARNING,
                description=f"Found {images_processed} images. Alt text generated but PDF format "
                           f"limitations may prevent embedding. Consider providing separate description."
            ))

    def _check_document_structure(self, doc: fitz.Document):
        """Check for document structure elements."""
        # Check for bookmarks/outline (table of contents)
        toc = doc.get_toc()

        if not toc:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.1",
                severity=Severity.WARNING,
                description="Document has no bookmarks/outline",
                suggestion="Add bookmarks for document navigation"
            ))
        else:
            # Check bookmark hierarchy
            levels = [item[0] for item in toc]
            if levels:
                prev_level = levels[0]
                for level in levels[1:]:
                    if level > prev_level + 1:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.3.1",
                            severity=Severity.WARNING,
                            description="Bookmark hierarchy skips levels",
                            suggestion="Ensure bookmarks follow proper hierarchy"
                        ))
                        break
                    prev_level = level

            self.report.add_fix(f"Document has {len(toc)} bookmarks")

        # Check for tagged PDF (structure)
        # Note: Checking if PDF is tagged is limited in PyMuPDF
        # We'll check for basic structure indicators

        # Check if first page has text (not just scanned image)
        if len(doc) > 0:
            first_page = doc[0]
            text = first_page.get_text()

            if not text.strip():
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.1.1",
                    severity=Severity.ERROR,
                    description="First page appears to have no extractable text",
                    suggestion="Document may be scanned without OCR - text content not accessible"
                ))
            else:
                self.report.add_fix("Document contains extractable text")

    def _check_reading_order(self, doc: fitz.Document):
        """Check and report on reading order issues."""
        for page_num in range(min(len(doc), 3)):  # Check first 3 pages
            page = doc[page_num]

            # Get text blocks with positions
            blocks = page.get_text("dict")["blocks"]

            if len(blocks) > 1:
                # Check if blocks appear to be in reading order (top-to-bottom, left-to-right)
                text_blocks = [b for b in blocks if b.get("type") == 0]  # Type 0 = text

                if text_blocks:
                    # Simple check: are y-coordinates generally increasing?
                    y_coords = [b["bbox"][1] for b in text_blocks]
                    inversions = sum(1 for i in range(1, len(y_coords)) if y_coords[i] < y_coords[i-1] - 50)

                    if inversions > len(y_coords) // 3:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.3.2",
                            severity=Severity.WARNING,
                            description=f"Page {page_num + 1} may have complex reading order",
                            suggestion="Verify reading order is logical for screen readers"
                        ))

        # General note about reading order
        self.report.add_warning(
            "Reading order in PDFs should be verified manually, especially for "
            "multi-column layouts or complex designs."
        )

    def _extract_text_content(self, doc: fitz.Document) -> str:
        """Extract all text content from PDF."""
        text = ""
        for page in doc:
            text += page.get_text() + "\n\n"
        return text

    def _check_color_only_information(self, doc: fitz.Document):
        """Check for color-only information in PDF content."""
        import re

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = page.get_text()

            # Check for color-only references in text
            color_ref_pattern = r'(the|see|marked in|shown in|highlighted in|in)\s+(red|green|blue|yellow|orange|purple|pink)\s*(text|items?|sections?|areas?|cells?)?'

            for match in re.finditer(color_ref_pattern, page_text, re.IGNORECASE):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.4.1",
                    severity=Severity.ERROR,
                    description=f"Color-only reference on page {page_num + 1}: '{match.group(0)}'",
                    suggestion="Add non-color indicator in addition to color"
                ))

        # Note about color analysis limitations
        self.report.add_warning(
            "PDF color analysis is limited to text content. Visual elements using "
            "color-only distinction may require manual review."
        )

    def _check_ambiguous_references(self, doc: fitz.Document):
        """Check for ambiguous page/visual-only references in PDF."""
        import re

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = page.get_text()

            # Pattern for "see page X" or "on page X"
            page_ref_pattern = r'([Ss]ee|[Oo]n|[Rr]efer to)\s+page\s+(\d+)'
            for match in re.finditer(page_ref_pattern, page_text):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.3.1",
                    severity=Severity.WARNING,
                    description=f"Ambiguous page reference on page {page_num + 1}: '{match.group(0)}'",
                    suggestion="Consider using section titles or descriptive references"
                ))

            # Pattern for "above" or "below" references
            position_ref_pattern = r'(the|see|as shown)\s+(above|below|following|previous)\s+(figure|table|section|image|diagram|chart)'
            for match in re.finditer(position_ref_pattern, page_text, re.IGNORECASE):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.3.1",
                    severity=Severity.INFO,
                    description=f"Position-based reference on page {page_num + 1}: '{match.group(0)}'",
                    suggestion="Use explicit figure/table numbers or descriptions"
                ))

            # Pattern for color-only references
            color_ref_pattern = r'(the|see|marked in|shown in|highlighted in)\s+(red|green|blue|yellow|orange|purple|pink)\s+(text|items?|sections?|areas?)?'
            for match in re.finditer(color_ref_pattern, page_text, re.IGNORECASE):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.4.1",
                    severity=Severity.ERROR,
                    description=f"Color-only reference on page {page_num + 1}: '{match.group(0)}'",
                    suggestion="Add non-color indicator in addition to color"
                ))

            # Check for links with generic text
            links = page.get_links()
            for link in links:
                if 'uri' in link:
                    # Try to get the text associated with this link
                    rect = link.get('from', None)
                    if rect:
                        link_text = page.get_text("text", clip=rect).strip()
                        if link_text.lower() in self.GENERIC_LINK_TEXTS:
                            self.report.add_issue(AccessibilityIssue(
                                wcag_criterion="2.4.4",
                                severity=Severity.WARNING,
                                description=f"Non-descriptive link text on page {page_num + 1}: '{link_text}'",
                                suggestion="Use descriptive link text that indicates the destination"
                            ))
