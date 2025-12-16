"""Base processor class for accessibility conversion."""

from abc import ABC, abstractmethod
from typing import Optional
from utils.accessibility import AccessibilityReport
from utils.claude_client import ClaudeClient


class BaseProcessor(ABC):
    """Abstract base class for document processors."""

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        """
        Initialize the processor.

        Args:
            claude_client: Optional ClaudeClient instance for AI-powered features
        """
        self.claude_client = claude_client or ClaudeClient()
        self.report = AccessibilityReport()

    @abstractmethod
    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process a document for accessibility.

        Args:
            content: Raw file content as bytes
            filename: Original filename for context

        Returns:
            Processed file content as bytes
        """
        pass

    @abstractmethod
    def get_file_extension(self) -> str:
        """Return the file extension this processor handles (e.g., '.html')."""
        pass

    def get_report(self) -> AccessibilityReport:
        """Get the accessibility report for the processed document."""
        return self.report

    def reset_report(self):
        """Reset the report for a new document."""
        self.report = AccessibilityReport()

    def _extract_text_context(self, content: str, position: int, window: int = 200) -> str:
        """
        Extract text around a position for context.

        Args:
            content: Full content string
            position: Position in the string
            window: Characters before and after to include

        Returns:
            Surrounding text for context
        """
        start = max(0, position - window)
        end = min(len(content), position + window)
        return content[start:end]
