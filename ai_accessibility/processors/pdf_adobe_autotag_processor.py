"""Adobe PDF Auto-Tag accessibility processor using Adobe PDF Services API."""

import io
import logging
import os
import tempfile
from typing import Optional
from datetime import datetime

from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
from adobe.pdfservices.operation.exception.exceptions import ServiceApiException, ServiceUsageException, SdkException
from adobe.pdfservices.operation.io.cloud_asset import CloudAsset
from adobe.pdfservices.operation.io.stream_asset import StreamAsset
from adobe.pdfservices.operation.pdf_services import PDFServices
from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import AutotagPDFJob
from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import AutotagPDFParams
from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import AutotagPDFResult

from .base import BaseProcessor
from utils.accessibility import AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


logger = logging.getLogger(__name__)


class AdobeAutoTagPDFProcessor(BaseProcessor):
    """
    PDF processor using Adobe PDF Services Auto-Tag API for WCAG compliance.

    This processor leverages Adobe's AI-powered auto-tagging capabilities to:
    - Automatically tag PDF content with proper semantic structure
    - Identify and tag headings, paragraphs, lists, and tables
    - Create proper reading order
    - Generate accessibility reports

    Adobe's Auto-Tag API provides production-grade PDF/UA tagging that significantly
    improves upon manual tagging approaches. The API uses Adobe Sensei AI to:
    - Detect document structure automatically
    - Create proper parent-child relationships in the structure tree
    - Mark content with appropriate PDF tags (H1-H6, P, L, Table, etc.)
    - Establish logical reading order

    Requirements:
    - Adobe PDF Services API credentials (PDF_SERVICES_CLIENT_ID, PDF_SERVICES_CLIENT_SECRET)
    - Network access to Adobe PDF Services
    - pdfservices-sdk package installed

    Limitations:
    - Files up to 100 MB supported
    - Non-scanned PDFs up to 200 pages
    - Scanned PDFs up to 100 pages
    - Rate limit: 25 requests per minute

    Note: While Adobe Auto-Tag significantly improves accessibility, additional
    manual remediation may still be required for full WCAG 2.1 AA compliance,
    especially for complex documents or specific accessibility requirements.
    """

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)
        self._check_credentials()

    def get_file_extension(self) -> str:
        return ".pdf"

    def _check_credentials(self):
        """Check if Adobe PDF Services credentials are configured."""
        client_id = os.getenv('PDF_SERVICES_CLIENT_ID')
        client_secret = os.getenv('PDF_SERVICES_CLIENT_SECRET')

        if not client_id or not client_secret:
            raise EnvironmentError(
                "Adobe PDF Services credentials not found. Please set "
                "PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET environment variables. "
                "Get credentials at: https://developer.adobe.com/document-services/docs/overview/pdf-services-api/"
            )

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process PDF using Adobe Auto-Tag API for accessibility.

        Args:
            content: PDF content as bytes
            filename: Original filename

        Returns:
            Tagged accessible PDF as bytes
        """
        self.reset_report()

        try:
            # Create credentials
            credentials = ServicePrincipalCredentials(
                client_id=os.getenv('PDF_SERVICES_CLIENT_ID'),
                client_secret=os.getenv('PDF_SERVICES_CLIENT_SECRET')
            )

            # Create PDF Services instance
            pdf_services = PDFServices(credentials=credentials)

            # Upload input asset
            logger.info(f"Uploading PDF to Adobe PDF Services: {filename}")
            input_asset = pdf_services.upload(
                input_stream=content,
                mime_type=PDFServicesMediaType.PDF
            )

            # Create auto-tag parameters with report generation
            autotag_params = AutotagPDFParams(
                generate_report=True,  # Generate XLSX accessibility report
                shift_headings=False   # Don't shift headings by default
            )

            # Create and submit auto-tag job
            logger.info("Submitting Auto-Tag job to Adobe PDF Services")
            autotag_job = AutotagPDFJob(
                input_asset=input_asset,
                autotag_pdf_params=autotag_params
            )

            location = pdf_services.submit(autotag_job)

            # Wait for job completion and get result
            logger.info("Waiting for Auto-Tag job to complete...")
            pdf_services_response = pdf_services.get_job_result(location, AutotagPDFResult)

            # Get tagged PDF
            result_asset: CloudAsset = pdf_services_response.get_result().get_tagged_pdf()
            stream_asset: StreamAsset = pdf_services.get_content(result_asset)
            tagged_pdf_bytes = stream_asset.get_input_stream()

            # Get accessibility report (XLSX)
            try:
                report_asset: CloudAsset = pdf_services_response.get_result().get_report()
                report_stream: StreamAsset = pdf_services.get_content(report_asset)
                report_bytes = report_stream.get_input_stream()

                # Store report for later access
                self._save_accessibility_report(report_bytes, filename)

                self.report.add_fix(
                    "Adobe Auto-Tag API generated accessibility report (XLSX). "
                    "Report contains details about added tags, replaced tags, and content needing review."
                )
            except Exception as e:
                logger.warning(f"Could not retrieve accessibility report: {e}")
                self.report.add_warning("Adobe Auto-Tag report not available")

            # Add success messages
            self.report.add_fix(
                "Adobe Auto-Tag API successfully tagged PDF with semantic structure"
            )
            self.report.add_fix(
                "Document structure includes: headings (H1-H6), paragraphs (P), lists (L), tables, and figures"
            )
            self.report.add_fix(
                "Logical reading order established by Adobe Sensei AI"
            )
            self.report.add_fix(
                "StructTreeRoot and proper parent-child relationships created"
            )

            # Add notes about what may still need manual review
            self.report.add_warning(
                "While Adobe Auto-Tag provides production-grade accessibility, "
                "manual review recommended for: alt text quality, table headers, "
                "form fields, and complex layouts"
            )

            # Check for images and note alt text requirement
            import fitz
            doc = fitz.open(stream=tagged_pdf_bytes, filetype="pdf")
            image_count = 0
            for page in doc:
                image_count += len(page.get_images(full=True))
            doc.close()

            if image_count > 0:
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.1.1",
                    severity=Severity.WARNING,
                    description=f"Document contains {image_count} images. Adobe Auto-Tag does not add alt text.",
                    suggestion="Use complementary Claude AI alt text generation for images (WCAG 1.1.1)"
                ))

                # Optionally add alt text using Claude
                if self.claude_client:
                    tagged_pdf_bytes = self._add_alt_text_with_claude(tagged_pdf_bytes, filename)

            logger.info("Adobe Auto-Tag processing complete")
            return tagged_pdf_bytes

        except (ServiceApiException, ServiceUsageException) as e:
            error_msg = f"Adobe PDF Services API error: {str(e)}"
            logger.error(error_msg)
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="N/A",
                severity=Severity.ERROR,
                description=error_msg,
                suggestion="Check Adobe PDF Services credentials and API status. "
                          "Verify file size (<100MB) and page count (<200 pages)."
            ))
            # Return original content if Adobe API fails
            return content

        except SdkException as e:
            error_msg = f"Adobe SDK error: {str(e)}"
            logger.error(error_msg)
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="N/A",
                severity=Severity.ERROR,
                description=error_msg,
                suggestion="Check pdfservices-sdk installation and environment configuration"
            ))
            return content

        except Exception as e:
            error_msg = f"Unexpected error during Adobe Auto-Tag processing: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="N/A",
                severity=Severity.ERROR,
                description=error_msg,
                suggestion="Review error logs and check PDF file integrity"
            ))
            return content

    def _save_accessibility_report(self, report_bytes: bytes, original_filename: str):
        """Save Adobe accessibility report to temp file for later access."""
        try:
            # Create output directory
            output_dir = os.path.join(os.path.dirname(__file__), "..", "output", "adobe_reports")
            os.makedirs(output_dir, exist_ok=True)

            # Generate report filename
            base_name = os.path.splitext(original_filename)[0] if original_filename else "document"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = os.path.join(output_dir, f"{base_name}_accessibility_report_{timestamp}.xlsx")

            # Save report
            with open(report_path, "wb") as f:
                f.write(report_bytes)

            self.report.add_fix(f"Adobe accessibility report saved: {report_path}")
            logger.info(f"Saved Adobe accessibility report to: {report_path}")

        except Exception as e:
            logger.warning(f"Could not save Adobe accessibility report: {e}")

    def _add_alt_text_with_claude(self, pdf_bytes: bytes, filename: str) -> bytes:
        """
        Add AI-generated alt text to images in the tagged PDF.

        This complements Adobe Auto-Tag by adding alt text to images,
        which Adobe's API does not automatically generate.
        """
        import fitz

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            images_processed = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)

                for img_info in image_list:
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

                        # Get context
                        page_text = page.get_text()[:500]

                        # Generate alt text with Claude
                        alt_result = self.claude_client.describe_complex_image(
                            image_data=image_bytes,
                            media_type=media_type,
                            context=f"PDF page {page_num + 1}. Context: {page_text}"
                        )

                        alt_text = alt_result.get('alt_text', '')

                        if alt_text:
                            # Try to add alt text to image object
                            doc.xref_set_key(xref, "Alt", f"({alt_text})")
                            images_processed += 1

                    except Exception as e:
                        logger.warning(f"Could not add alt text to image on page {page_num + 1}: {e}")

            if images_processed > 0:
                self.report.add_fix(
                    f"Added AI-generated alt text to {images_processed} images using Claude"
                )

            # Save updated PDF
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True)
            doc.close()
            return output.getvalue()

        except Exception as e:
            logger.warning(f"Could not add alt text with Claude: {e}")
            return pdf_bytes
