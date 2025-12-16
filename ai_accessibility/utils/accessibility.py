"""WCAG 2.1 AA accessibility checking utilities."""

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Severity(Enum):
    """Severity levels for accessibility issues."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class AccessibilityIssue:
    """Represents a single accessibility issue."""
    wcag_criterion: str
    severity: Severity
    description: str
    location: str = ""
    suggestion: str = ""
    auto_fixed: bool = False


@dataclass
class AccessibilityReport:
    """Report of accessibility analysis and fixes."""
    issues: list[AccessibilityIssue] = field(default_factory=list)
    fixes_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    original_score: int = 0
    final_score: int = 0

    def add_issue(self, issue: AccessibilityIssue):
        self.issues.append(issue)

    def add_fix(self, description: str):
        self.fixes_applied.append(description)

    def add_warning(self, warning: str):
        self.warnings.append(warning)

    def to_dict(self) -> dict:
        return {
            "issues": [
                {
                    "wcag_criterion": i.wcag_criterion,
                    "severity": i.severity.value,
                    "description": i.description,
                    "location": i.location,
                    "suggestion": i.suggestion,
                    "auto_fixed": i.auto_fixed
                }
                for i in self.issues
            ],
            "fixes_applied": self.fixes_applied,
            "warnings": self.warnings,
            "original_score": self.original_score,
            "final_score": self.final_score,
            "summary": self.get_summary()
        }

    def get_summary(self) -> str:
        error_count = sum(1 for i in self.issues if i.severity == Severity.ERROR and not i.auto_fixed)
        warning_count = sum(1 for i in self.issues if i.severity == Severity.WARNING and not i.auto_fixed)
        fixed_count = len(self.fixes_applied)

        return (
            f"Accessibility Report: {fixed_count} issues auto-fixed, "
            f"{error_count} errors remaining, {warning_count} warnings remaining"
        )


class AccessibilityChecker:
    """Utility class for checking WCAG 2.1 AA compliance."""

    # WCAG 2.1 AA criteria we check
    CRITERIA = {
        "1.1.1": "Non-text Content",
        "1.3.1": "Info and Relationships",
        "1.3.2": "Meaningful Sequence",
        "1.4.3": "Contrast (Minimum)",
        "2.4.1": "Bypass Blocks",
        "2.4.2": "Page Titled",
        "2.4.4": "Link Purpose (In Context)",
        "2.4.6": "Headings and Labels",
        "3.1.1": "Language of Page",
        "4.1.1": "Parsing",
        "4.1.2": "Name, Role, Value",
    }

    # Generic/non-descriptive link texts to flag
    GENERIC_LINK_TEXTS = {
        "click here", "click", "here", "read more", "learn more",
        "more", "link", "this link", "info", "information",
        "details", "more details", "continue", "go", "download"
    }

    @staticmethod
    def check_heading_hierarchy(headings: list[tuple[int, str]]) -> list[AccessibilityIssue]:
        """
        Check if heading hierarchy is valid.

        Args:
            headings: List of (level, text) tuples, e.g., [(1, "Title"), (2, "Section")]

        Returns:
            List of issues found
        """
        issues = []

        if not headings:
            return issues

        # Check for h1
        h1_count = sum(1 for level, _ in headings if level == 1)
        if h1_count == 0:
            issues.append(AccessibilityIssue(
                wcag_criterion="2.4.6",
                severity=Severity.ERROR,
                description="Document has no h1 heading",
                suggestion="Add an h1 heading as the main title"
            ))
        elif h1_count > 1:
            issues.append(AccessibilityIssue(
                wcag_criterion="2.4.6",
                severity=Severity.WARNING,
                description=f"Document has {h1_count} h1 headings (typically should have one)",
                suggestion="Consider using only one h1 for the main title"
            ))

        # Check for skipped levels
        prev_level = 0
        for i, (level, text) in enumerate(headings):
            if prev_level > 0 and level > prev_level + 1:
                issues.append(AccessibilityIssue(
                    wcag_criterion="1.3.1",
                    severity=Severity.ERROR,
                    description=f"Heading level skipped from h{prev_level} to h{level}",
                    location=f"Heading: '{text[:50]}...' " if len(text) > 50 else f"Heading: '{text}'",
                    suggestion=f"Use h{prev_level + 1} instead of h{level}"
                ))
            prev_level = level

        return issues

    @staticmethod
    def check_link_text(text: str, href: str = "") -> Optional[AccessibilityIssue]:
        """
        Check if link text is descriptive.

        Args:
            text: The link text
            href: The link URL (for context)

        Returns:
            AccessibilityIssue if problematic, None otherwise
        """
        normalized = text.lower().strip()

        if normalized in AccessibilityChecker.GENERIC_LINK_TEXTS:
            return AccessibilityIssue(
                wcag_criterion="2.4.4",
                severity=Severity.WARNING,
                description=f"Generic link text: '{text}'",
                location=f"Link to: {href[:50]}" if href else "",
                suggestion="Use descriptive text that indicates the link destination"
            )

        if len(normalized) < 2:
            return AccessibilityIssue(
                wcag_criterion="2.4.4",
                severity=Severity.WARNING,
                description=f"Link text too short: '{text}'",
                location=f"Link to: {href[:50]}" if href else "",
                suggestion="Use more descriptive link text"
            )

        # Check for URL as link text
        if re.match(r'^https?://', normalized):
            return AccessibilityIssue(
                wcag_criterion="2.4.4",
                severity=Severity.WARNING,
                description="URL used as link text",
                location=f"Link: {text[:50]}",
                suggestion="Use descriptive text instead of the URL"
            )

        return None

    @staticmethod
    def check_image_alt(alt: Optional[str], is_decorative: bool = False) -> Optional[AccessibilityIssue]:
        """
        Check if image alt text is appropriate.

        Args:
            alt: The alt text (None if missing)
            is_decorative: Whether the image is decorative

        Returns:
            AccessibilityIssue if problematic, None otherwise
        """
        if alt is None:
            return AccessibilityIssue(
                wcag_criterion="1.1.1",
                severity=Severity.ERROR,
                description="Image missing alt attribute",
                suggestion="Add alt text describing the image content, or alt='' if decorative"
            )

        if not is_decorative and alt == "":
            # Empty alt on non-decorative image
            return AccessibilityIssue(
                wcag_criterion="1.1.1",
                severity=Severity.WARNING,
                description="Image has empty alt text but may not be decorative",
                suggestion="Add descriptive alt text if image conveys information"
            )

        # Check for unhelpful alt text patterns
        unhelpful_patterns = [
            r'^image$',
            r'^img$',
            r'^picture$',
            r'^photo$',
            r'^graphic$',
            r'^image\s*\d*$',
            r'^img_\d+',
            r'^DSC\d+',
            r'^IMG_\d+',
            r'^screenshot',
            r'^untitled',
        ]

        if alt:
            normalized = alt.lower().strip()
            for pattern in unhelpful_patterns:
                if re.match(pattern, normalized):
                    return AccessibilityIssue(
                        wcag_criterion="1.1.1",
                        severity=Severity.WARNING,
                        description=f"Image has non-descriptive alt text: '{alt}'",
                        suggestion="Use alt text that describes the image content"
                    )

        return None

    @staticmethod
    def check_table_accessibility(
        has_headers: bool,
        has_caption: bool,
        has_scope: bool = False
    ) -> list[AccessibilityIssue]:
        """
        Check table accessibility features.

        Args:
            has_headers: Whether table has th elements
            has_caption: Whether table has a caption
            has_scope: Whether th elements have scope attributes

        Returns:
            List of issues found
        """
        issues = []

        if not has_headers:
            issues.append(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.ERROR,
                description="Table missing header cells (th elements)",
                suggestion="Add th elements for column/row headers"
            ))

        if not has_caption:
            issues.append(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description="Table missing caption",
                suggestion="Add a caption element describing the table's purpose"
            ))

        if has_headers and not has_scope:
            issues.append(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.INFO,
                description="Table headers missing scope attribute",
                suggestion="Add scope='col' or scope='row' to th elements"
            ))

        return issues

    @staticmethod
    def check_form_labels(inputs_with_labels: list[tuple[str, bool, str]]) -> list[AccessibilityIssue]:
        """
        Check form input label associations.

        Args:
            inputs_with_labels: List of (input_id, has_label, input_type) tuples

        Returns:
            List of issues found
        """
        issues = []

        for input_id, has_label, input_type in inputs_with_labels:
            if not has_label and input_type not in ("hidden", "submit", "button", "reset", "image"):
                issues.append(AccessibilityIssue(
                    wcag_criterion="4.1.2",
                    severity=Severity.ERROR,
                    description=f"Form input missing associated label",
                    location=f"Input: {input_id or '(no id)'} ({input_type})",
                    suggestion="Add a label element with for attribute matching the input id"
                ))

        return issues

    @staticmethod
    def estimate_color_contrast(fg_hex: str, bg_hex: str) -> tuple[float, bool]:
        """
        Estimate contrast ratio between two colors.

        Args:
            fg_hex: Foreground color in hex (e.g., "#000000")
            bg_hex: Background color in hex

        Returns:
            Tuple of (contrast_ratio, meets_aa_normal_text)
        """
        def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
            hex_color = hex_color.lstrip('#')
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        def relative_luminance(rgb: tuple[int, int, int]) -> float:
            r, g, b = [x / 255 for x in rgb]
            r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
            g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
            b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        try:
            fg_rgb = hex_to_rgb(fg_hex)
            bg_rgb = hex_to_rgb(bg_hex)

            l1 = relative_luminance(fg_rgb)
            l2 = relative_luminance(bg_rgb)

            lighter = max(l1, l2)
            darker = min(l1, l2)

            ratio = (lighter + 0.05) / (darker + 0.05)

            # WCAG AA requires 4.5:1 for normal text, 3:1 for large text
            return ratio, ratio >= 4.5
        except (ValueError, IndexError):
            return 0, False

    @staticmethod
    def fix_heading_hierarchy(headings: list[tuple[int, str]]) -> list[tuple[int, str, int]]:
        """
        Suggest fixed heading levels.

        Args:
            headings: List of (level, text) tuples

        Returns:
            List of (original_level, text, suggested_level) tuples
        """
        if not headings:
            return []

        fixed = []
        min_level = min(level for level, _ in headings)
        prev_fixed_level = 0

        for level, text in headings:
            # Normalize so minimum is h1
            normalized = level - min_level + 1

            # Don't skip more than one level from previous
            if prev_fixed_level > 0 and normalized > prev_fixed_level + 1:
                suggested = prev_fixed_level + 1
            else:
                suggested = normalized

            fixed.append((level, text, suggested))
            prev_fixed_level = suggested

        return fixed
