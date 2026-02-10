"""Document processors for accessibility conversion."""

from .base import BaseProcessor
from .html_processor import HTMLProcessor
from .markdown_processor import MarkdownProcessor
from .qmd_processor import QMDProcessor
from .latex_processor import LaTeXProcessor
from .pdf_processor import PDFProcessor
try:
    from .pdf_adobe_autotag_processor import AdobeAutoTagPDFProcessor
except ImportError:
    AdobeAutoTagPDFProcessor = None
from .pdf_to_qmd_processor import PDFToQMDProcessor
from .pptx_processor import PowerPointProcessor

__all__ = [
    "BaseProcessor",
    "HTMLProcessor",
    "MarkdownProcessor",
    "QMDProcessor",
    "LaTeXProcessor",
    "PDFProcessor",
    "AdobeAutoTagPDFProcessor",
    "PDFToQMDProcessor",
    "PowerPointProcessor",
]
