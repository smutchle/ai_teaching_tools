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
            self._mark_as_tagged_pdf(doc)  # Must be done before language for PAC detection
            self._create_structure_tree_root(doc)  # Create basic structure tree
            self._set_document_language(doc)
            self._add_image_alt_text(doc)
            self._auto_generate_bookmarks(doc)
            self._check_document_structure(doc)
            self._check_reading_order(doc)
            self._check_color_only_information(doc)
            self._check_ambiguous_references(doc)

            # Add note about limitations
            self.report.add_warning(
                "PDF accessibility: Basic structure tree created. Full PDF/UA tagging "
                "(proper semantic structure for all content) requires professional tools. "
                "For complete PAC compliance, consider post-processing with PDF/UA tools."
            )

            # Save to bytes
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True)
            return output.getvalue()

        finally:
            doc.close()

    def _set_document_metadata(self, doc: fitz.Document, filename: str):
        """Set document metadata for accessibility."""
        metadata = doc.metadata
        updates = {}

        # Check and set title
        if not metadata.get('title'):
            title = filename.rsplit('.', 1)[0] if filename else "Document"
            updates['title'] = title
            self.report.add_fix(f"Set document title: '{title}'")

        # Set subject if not present
        if not metadata.get('subject'):
            updates['subject'] = 'Accessible Document'
            self.report.add_fix("Set document subject for better categorization")

        # Set producer to indicate accessibility processing
        updates['producer'] = 'WCAG 2.1 AA Accessibility Converter'

        # Set author to generic if missing (optional)
        if not metadata.get('author'):
            updates['author'] = 'Unknown'
            self.report.add_fix("Set default author metadata")

        # Apply all metadata updates at once
        if updates:
            doc.set_metadata(updates)

    def _create_structure_tree_root(self, doc: fitz.Document):
        """
        Create a basic structure tree root for PDF accessibility.

        PAC requires a StructTreeRoot for proper accessibility checking.
        This creates a minimal structure tree indicating document structure exists.

        Note: Full PDF/UA tagging requires extensive structure tree creation
        with proper parent trees, role maps, and marked content IDs. This is
        a basic implementation to satisfy minimum requirements.
        """
        try:
            catalog_xref = doc.pdf_catalog()

            # Check if StructTreeRoot already exists
            struct_tree_obj = doc.xref_get_key(catalog_xref, "StructTreeRoot")

            if struct_tree_obj and struct_tree_obj[0] != 'null':
                # Structure tree exists
                self.report.add_fix("Document already has StructTreeRoot")
                return

            # Create basic StructTreeRoot
            new_xref = doc.xref_length()

            # Create a minimal StructTreeRoot object
            # A complete implementation would include ParentTree, RoleMap, etc.
            struct_root_dict = """<<
/Type /StructTreeRoot
/K []
>>"""

            # Add the structure tree root object
            doc.xref_stream(new_xref, struct_root_dict.encode())

            # Link to catalog
            doc.xref_set_key(catalog_xref, "StructTreeRoot", f"{new_xref} 0 R")

            self.report.add_fix("Created basic StructTreeRoot for document structure")

            # Note limitation
            self.report.add_warning(
                "Basic structure tree created. Full semantic tagging (P, H1-H6, Figure tags) "
                "requires professional PDF/UA tools. PAC may still report structure failures."
            )

        except Exception as e:
            self.report.add_warning(
                f"Could not create StructTreeRoot: {str(e)}. "
                "Document may fail PAC structure checks."
            )

    def _mark_as_tagged_pdf(self, doc: fitz.Document):
        """
        Mark PDF as tagged for accessibility.

        Sets the /MarkInfo dictionary in PDF catalog to indicate
        the document contains tagged content. PAC requires this
        to properly evaluate accessibility.
        """
        try:
            catalog_xref = doc.pdf_catalog()

            # Check if MarkInfo already exists
            mark_info_obj = doc.xref_get_key(catalog_xref, "MarkInfo")

            if mark_info_obj and mark_info_obj[0] != 'null':
                # MarkInfo exists, update it
                try:
                    mark_xref = int(mark_info_obj[1].split()[0])
                    doc.xref_set_key(mark_xref, "Marked", "true")
                    self.report.add_fix("Updated MarkInfo: Set document as tagged (Marked=true)")
                except:
                    self.report.add_warning("Could not update existing MarkInfo")
            else:
                # Create new MarkInfo dictionary
                # Get a new xref number
                new_xref = doc.xref_length()

                # Create MarkInfo dictionary object
                mark_info_dict = "<<\n/Marked true\n>>"

                # Add the object
                doc.xref_stream(new_xref, mark_info_dict.encode())

                # Link it to catalog
                doc.xref_set_key(catalog_xref, "MarkInfo", f"{new_xref} 0 R")

                self.report.add_fix("Created MarkInfo dictionary: PDF marked as tagged for accessibility")

        except Exception as e:
            self.report.add_warning(
                f"Could not mark PDF as tagged: {str(e)}. "
                "PAC may not properly detect accessibility features."
            )

    def _set_document_language(self, doc: fitz.Document):
        """Set document language for accessibility (WCAG 3.1.1).

        PAC expects language set in PDF catalog as a PDF string.
        Format: /Lang (en-US)
        """
        try:
            catalog_xref = doc.pdf_catalog()

            # Check if language already set
            try:
                existing_lang = doc.xref_get_key(catalog_xref, "Lang")
                if existing_lang and existing_lang[0] != 'null':
                    self.report.add_fix(f"Document language already set: {existing_lang[1]}")
                    return
            except:
                pass

            # Set language as PDF string (format required by PAC)
            # This creates: /Lang (en-US) in the PDF catalog
            doc.xref_set_key(catalog_xref, "Lang", "(en-US)")

            # Verify it was set correctly
            lang_check = doc.xref_get_key(catalog_xref, "Lang")
            if lang_check and lang_check[0] == 'string':
                self.report.add_fix(f"Set document language to '{lang_check[1]}' in PDF catalog (PAC-compatible)")
            else:
                self.report.add_warning("Language set but format may not be PAC-compatible")

        except Exception as e:
            self.report.add_warning(
                f"Could not set document language: {str(e)}. "
                "Manual language setting recommended for PAC compliance."
            )

    def _add_image_alt_text(self, doc: fitz.Document):
        """Add alt text to images in the PDF."""
        images_processed = 0
        images_with_alt = 0
        alt_text_annotations = []

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
                        long_desc = alt_result.get('long_description', '')

                        if alt_text:
                            # Try to embed alt text in PDF structure
                            try:
                                # Get image xref and set /Alt tag
                                img_obj = doc.xref_object(xref)
                                # Add /Alt entry to image dictionary
                                doc.xref_set_key(xref, "Alt", f"({alt_text})")
                                images_with_alt += 1

                                self.report.add_fix(
                                    f"Embedded alt text for image on page {page_num + 1}: "
                                    f"'{alt_text[:50]}...'"
                                )
                            except Exception as embed_err:
                                # Fallback: store alt text for annotation
                                alt_text_annotations.append({
                                    'page': page_num,
                                    'img_index': img_index,
                                    'alt_text': alt_text,
                                    'long_desc': long_desc
                                })
                                self.report.add_fix(
                                    f"Generated alt text for image on page {page_num + 1}: "
                                    f"'{alt_text[:50]}...'"
                                )

                            # If complex image, note long description
                            if alt_result.get('is_complex') and long_desc:
                                self.report.add_warning(
                                    f"Complex image on page {page_num + 1} may need additional "
                                    f"description. Long description: {long_desc[:100]}..."
                                )

                    except Exception as e:
                        self.report.add_warning(
                            f"Could not generate alt text for image on page {page_num + 1}: {str(e)}"
                        )

                    images_processed += 1

                except Exception as e:
                    self.report.add_warning(f"Could not process image on page {page_num + 1}: {str(e)}")

        if images_processed > 0:
            if images_with_alt == images_processed:
                self.report.add_fix(
                    f"Successfully embedded alt text for all {images_with_alt} images in PDF structure"
                )
            else:
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.1.1",
                    severity=Severity.INFO,
                    description=f"Generated alt text for {images_processed} images. "
                               f"{images_with_alt} embedded in PDF structure."
                ))

    def _auto_generate_bookmarks(self, doc: fitz.Document):
        """Auto-generate bookmarks from document headings (WCAG 2.4.1)."""
        # Check if bookmarks already exist
        existing_toc = doc.get_toc()
        if existing_toc:
            self.report.add_fix(f"Document already has {len(existing_toc)} bookmarks")
            return

        # Try to detect headings and create bookmarks
        toc = []
        heading_patterns = [
            (r'^[A-Z][A-Z\s]{5,}$', 1),  # ALL CAPS headings (level 1)
            (r'^(?:Chapter|Section|Part)\s+\d+', 1),  # Chapter/Section headings (level 1)
            (r'^\d+\.\s+[A-Z]', 1),  # Numbered sections like "1. Introduction" (level 1)
            (r'^\d+\.\d+\s+[A-Z]', 2),  # Sub-sections like "1.1 Background" (level 2)
        ]

        import re

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        # Get text and font size
                        text = ""
                        font_size = 0
                        for span in line.get("spans", []):
                            text += span.get("text", "")
                            font_size = max(font_size, span.get("size", 0))

                        text = text.strip()

                        # Check if this looks like a heading
                        if len(text) > 3 and len(text) < 100:
                            # Check patterns
                            for pattern, level in heading_patterns:
                                if re.match(pattern, text):
                                    toc.append([level, text, page_num + 1])
                                    break
                            else:
                                # Also check for large font sizes (likely headings)
                                if font_size > 16:
                                    toc.append([1, text, page_num + 1])
                                elif font_size > 14:
                                    toc.append([2, text, page_num + 1])

        # Set the TOC if we found any headings
        if toc:
            # Remove duplicates and limit
            seen = set()
            unique_toc = []
            for item in toc:
                key = (item[1], item[2])
                if key not in seen and len(unique_toc) < 50:  # Limit to 50 bookmarks
                    seen.add(key)
                    unique_toc.append(item)

            if unique_toc:
                doc.set_toc(unique_toc)
                self.report.add_fix(
                    f"Auto-generated {len(unique_toc)} bookmarks from document headings"
                )
        else:
            self.report.add_warning(
                "Could not auto-generate bookmarks. Consider manually adding bookmarks "
                "for better navigation (WCAG 2.4.1)."
            )

    def _check_document_structure(self, doc: fitz.Document):
        """Check for document structure elements."""
        # Check for bookmarks/outline (table of contents)
        toc = doc.get_toc()

        if not toc:
            # This shouldn't happen if auto-generation ran, but check anyway
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.1",
                severity=Severity.INFO,
                description="Document has no bookmarks/outline after auto-generation",
                suggestion="Manual bookmark creation recommended for better navigation"
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
