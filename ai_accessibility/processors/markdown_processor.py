"""Markdown accessibility processor."""

import re
from typing import Optional
from .base import BaseProcessor
from utils.accessibility import AccessibilityChecker, AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


class MarkdownProcessor(BaseProcessor):
    """Processor for Markdown documents."""

    # Generic/non-descriptive link texts to fix
    GENERIC_LINK_TEXTS = {
        'here', 'click here', 'click', 'link', 'this link',
        'read more', 'learn more', 'more', 'info', 'details'
    }

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)

    def get_file_extension(self) -> str:
        return ".md"

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process Markdown for WCAG 2.1 AA accessibility.

        Args:
            content: Markdown content as bytes
            filename: Original filename

        Returns:
            Accessible Markdown as bytes
        """
        self.reset_report()

        md_str = content.decode('utf-8', errors='replace')

        # Apply accessibility fixes
        md_str = self._fix_heading_hierarchy(md_str)
        md_str = self._add_image_alt_text(md_str)
        md_str = self._fix_link_text(md_str)
        md_str = self._add_code_language(md_str)
        md_str = self._add_table_headers(md_str)
        md_str = self._fix_color_only_information(md_str)
        md_str = self._add_math_descriptions(md_str)
        md_str = self._fix_ambiguous_references(md_str)
        md_str = self._check_document_structure(md_str)

        return md_str.encode('utf-8')

    def _fix_heading_hierarchy(self, content: str) -> str:
        """Fix heading hierarchy in Markdown."""
        # Find all headings (both # style and underline style)
        heading_pattern = r'^(#{1,6})\s+(.+)$'
        headings = []
        lines = content.split('\n')

        for i, line in enumerate(lines):
            match = re.match(heading_pattern, line)
            if match:
                level = len(match.group(1))
                text = match.group(2).strip()
                headings.append((i, level, text))

        if not headings:
            return content

        # Check issues
        heading_data = [(level, text) for _, level, text in headings]
        issues = AccessibilityChecker.check_heading_hierarchy(heading_data)
        for issue in issues:
            self.report.add_issue(issue)

        # Get fixes
        fixes = AccessibilityChecker.fix_heading_hierarchy(heading_data)

        # Apply fixes in reverse order (to preserve line numbers)
        for i in range(len(fixes) - 1, -1, -1):
            orig_level, text, suggested = fixes[i]
            line_num = headings[i][0]

            if orig_level != suggested:
                new_heading = '#' * suggested + ' ' + text
                lines[line_num] = new_heading
                self.report.add_fix(
                    f"Changed h{orig_level} to h{suggested}: '{text[:30]}...'"
                    if len(text) > 30 else f"Changed h{orig_level} to h{suggested}: '{text}'"
                )

        return '\n'.join(lines)

    def _add_image_alt_text(self, content: str) -> str:
        """Add alt text to images missing it."""
        # Pattern for images: ![alt](src) or ![alt](src "title")
        img_pattern = r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)'

        def replace_image(match):
            alt = match.group(1)
            src = match.group(2)
            full_match = match.group(0)

            # Check if alt text is missing or empty
            if not alt.strip():
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.1.1",
                    severity=Severity.ERROR,
                    description="Image missing alt text",
                    location=f"Image: {src[:50]}"
                ))

                # Generate alt text
                try:
                    new_alt = self.claude_client.generate_alt_text(
                        image_context=f"Markdown image with source: {src}"
                    )

                    if new_alt == "DECORATIVE":
                        # Keep empty alt for decorative images
                        self.report.add_fix(f"Image marked as decorative: {src[:50]}")
                        return full_match
                    else:
                        self.report.add_fix(f"Added alt text for: {src[:50]}")
                        return f'![{new_alt}]({src})'
                except Exception as e:
                    self.report.add_warning(f"Could not generate alt text: {str(e)}")
                    return full_match
            else:
                # Check if existing alt is helpful
                issue = AccessibilityChecker.check_image_alt(alt)
                if issue:
                    self.report.add_issue(issue)

            return full_match

        return re.sub(img_pattern, replace_image, content)

    def _fix_link_text(self, content: str) -> str:
        """Fix non-descriptive link text."""
        # Pattern for links: [text](url) or [text](url "title")
        link_pattern = r'\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)'

        links_to_improve = []

        def collect_links(match):
            text = match.group(1)
            href = match.group(2)
            title = match.group(3) if match.group(3) else None

            # Check if this is generic link text
            is_generic = text.lower().strip() in self.GENERIC_LINK_TEXTS

            if is_generic:
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="2.4.4",
                    severity=Severity.ERROR,
                    description=f"Non-descriptive link text: '{text}'",
                    suggestion="Use descriptive link text that indicates the destination"
                ))
                links_to_improve.append({
                    'text': text,
                    'href': href,
                    'title': title,
                    'match': match.group(0),
                    'start': match.start(),
                    'end': match.end()
                })
            else:
                # Check using AccessibilityChecker for other issues
                issue = AccessibilityChecker.check_link_text(text, href)
                if issue:
                    self.report.add_issue(issue)

            return match.group(0)

        # First pass: collect problematic links
        re.sub(link_pattern, collect_links, content)

        # Get improvements from Claude and actually fix the link text
        if links_to_improve:
            try:
                improvements = self.claude_client.improve_link_text(
                    [{'text': l['text'], 'href': l['href']} for l in links_to_improve]
                )

                # Apply improvements (in reverse order to preserve positions)
                for i in range(len(improvements) - 1, -1, -1):
                    if i < len(links_to_improve) and improvements[i].get('needs_change'):
                        link = links_to_improve[i]
                        improved_text = improvements[i].get('improved', link['text'])

                        # Actually change the link text (not just add title)
                        if link['title']:
                            new_link = f"[{improved_text}]({link['href']} \"{link['title']}\")"
                        else:
                            new_link = f"[{improved_text}]({link['href']})"

                        content = content[:link['start']] + new_link + content[link['end']:]
                        self.report.add_fix(f"Changed link text from '{link['text']}' to '{improved_text}'")

            except Exception as e:
                self.report.add_warning(f"Could not improve link text: {str(e)}")

        return content

    def _add_code_language(self, content: str) -> str:
        """Add language identifiers to code blocks."""
        # Pattern for fenced code blocks without language
        pattern = r'^```\s*$'

        lines = content.split('\n')
        modified = False

        for i, line in enumerate(lines):
            if re.match(pattern, line):
                # Look at the next line to guess the language
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    lang = self._guess_language(next_line)
                    if lang:
                        lines[i] = f'```{lang}'
                        modified = True
                        self.report.add_fix(f"Added language identifier: {lang}")

        if modified:
            return '\n'.join(lines)
        return content

    def _guess_language(self, code_line: str) -> str:
        """Guess programming language from a code line."""
        patterns = {
            'python': [r'^(import |from |def |class |if __name__|print\()', r'\.py'],
            'javascript': [r'^(const |let |var |function |import |export )', r'console\.log', r'=>'],
            'java': [r'^(public |private |protected |class |import java)', r'System\.out'],
            'html': [r'^<(!DOCTYPE|html|head|body|div|p |span|a |img )', r'</'],
            'css': [r'^\s*[.#]?[\w-]+\s*\{', r':\s*(#|rgb|px|em|rem)'],
            'sql': [r'^(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s', r'FROM\s'],
            'bash': [r'^(#!/bin/|echo |cd |ls |mkdir |chmod |sudo )', r'\$\('],
            'json': [r'^\s*[\[{]', r'"\w+":\s*'],
            'yaml': [r'^\w+:\s*$', r'^-\s+\w+'],
            'xml': [r'^<\?xml', r'<[\w:]+[^>]*>'],
        }

        for lang, lang_patterns in patterns.items():
            for p in lang_patterns:
                if re.search(p, code_line, re.IGNORECASE):
                    return lang

        return 'text'

    def _add_table_headers(self, content: str) -> str:
        """Ensure tables have proper header rows."""
        # Markdown tables should have header row followed by separator
        # | Header 1 | Header 2 |
        # |----------|----------|
        # | Data 1   | Data 2   |

        # Pattern to find tables without proper headers
        table_pattern = r'^\|([^|]+\|)+\s*$'

        lines = content.split('\n')
        in_table = False
        table_start = -1

        for i, line in enumerate(lines):
            if re.match(table_pattern, line.strip()):
                if not in_table:
                    in_table = True
                    table_start = i

                # Check if next line is separator
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # Separator line should look like |---|---|
                    if not re.match(r'^\|(\s*:?-+:?\s*\|)+\s*$', next_line):
                        # This might be a table without headers
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.3.1",
                            severity=Severity.WARNING,
                            description="Table may be missing header row",
                            location=f"Line {i + 1}"
                        ))
            else:
                if in_table and not re.match(r'^\|', line.strip()):
                    in_table = False
                    table_start = -1

        return content

    def _fix_color_only_information(self, content: str) -> str:
        """Fix color-only information in Markdown by adding additional visual indicators."""
        # Pattern for HTML-style colored text: <span style="color:...">text</span>
        # Handle both "color:red" and "color: red;" formats
        span_color_pattern = r'<span\s+style=["\']color:\s*([^"\';\s]+)[;\s]*["\']>([^<]+)</span>'

        def fix_span_color(match):
            color = match.group(1)
            text = match.group(2)

            # Skip if already has additional formatting (bold/strong tags or markdown)
            if '**' in text or '<strong>' in text or '<b>' in text:
                return match.group(0)

            # Add bold formatting along with color for emphasis
            new_text = f'<span style="color:{color};"><strong>{text}</strong></span>'
            self.report.add_fix(f"Added bold to color-emphasized text: '{text[:30]}...'" if len(text) > 30 else f"Added bold to color-emphasized text: '{text}'")
            return new_text

        content = re.sub(span_color_pattern, fix_span_color, content, flags=re.IGNORECASE)

        # Pattern for inline HTML with class-based colors
        class_color_pattern = r'<span\s+class=["\']([^"\']*(?:red|green|blue|warning|error|success|danger)[^"\']*)["\']>([^<]+)</span>'

        def fix_class_color(match):
            classes = match.group(1)
            text = match.group(2)

            # Skip if already has additional formatting
            if '**' in text or '<strong>' in text or '<b>' in text:
                return match.group(0)

            # Add bold formatting
            new_text = f'<span class="{classes}"><strong>{text}</strong></span>'
            self.report.add_fix(f"Added bold to class-colored text: '{text[:30]}...'" if len(text) > 30 else f"Added bold to class-colored text: '{text}'")
            return new_text

        return re.sub(class_color_pattern, fix_class_color, content, flags=re.IGNORECASE)

    def _add_math_descriptions(self, content: str) -> str:
        """Add descriptions for mathematical equations in Markdown."""
        # Pattern for display math ($$...$$)
        display_math_pattern = r'\$\$([^$]+)\$\$'

        matches = list(re.finditer(display_math_pattern, content, re.DOTALL))

        for match in reversed(matches):  # Reverse to preserve positions
            math_content = match.group(1).strip()

            # Check if there's descriptive text before the equation
            preceding_text = content[max(0, match.start()-200):match.start()]

            # Look for description keywords
            has_description = any(keyword in preceding_text.lower() for keyword in [
                'where', 'equation', 'formula', 'defined as', 'given by',
                'expressed as', 'calculated', 'represents', 'shows'
            ])

            if not has_description:
                # Generate description using Claude
                try:
                    description = self._generate_math_description(math_content)
                    if description:
                        # Add description before the equation
                        readable_desc = f"\nThe following equation {description}:\n"
                        content = content[:match.start()] + readable_desc + content[match.start():]
                        self.report.add_fix(f"Added description for equation")
                except Exception:
                    self.report.add_issue(AccessibilityIssue(
                        wcag_criterion="1.1.1",
                        severity=Severity.WARNING,
                        description="Mathematical equation lacks textual description",
                        suggestion="Add context explaining what the equation represents"
                    ))

        # Also check for inline math that's complex
        inline_math_pattern = r'(?<!\$)\$([^$]+)\$(?!\$)'
        inline_matches = list(re.finditer(inline_math_pattern, content))

        complex_inline_count = 0
        for match in inline_matches:
            math_content = match.group(1)
            # Flag complex inline math (fractions, integrals, etc.)
            if any(cmd in math_content for cmd in ['\\frac', '\\int', '\\sum', '\\prod', '\\lim']):
                complex_inline_count += 1

        if complex_inline_count > 0:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.1.1",
                severity=Severity.INFO,
                description=f"Found {complex_inline_count} complex inline math expressions",
                suggestion="Consider adding explanatory text for complex mathematical notation"
            ))

        return content

    def _generate_math_description(self, math_content: str) -> str:
        """Generate description for math equation using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Describe what this mathematical equation represents in plain language (one brief phrase starting with a verb). Return ONLY the description.",
                messages=[{
                    "role": "user",
                    "content": f"Math equation: {math_content}\n\nDescribe what this represents:"
                }]
            )
            return response.content[0].text.strip().lower()
        except Exception:
            return None

    def _fix_ambiguous_references(self, content: str) -> str:
        """Fix ambiguous page/visual-only references in Markdown."""
        # Pattern for "see page X" or "on page X"
        page_ref_pattern = r'([Ss]ee|[Oo]n|[Rr]efer to)\s+page\s+(\d+)'

        matches = list(re.finditer(page_ref_pattern, content))

        for match in matches:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description=f"Ambiguous page reference: '{match.group(0)}'",
                suggestion="Use descriptive links or section references instead of page numbers"
            ))

        # Pattern for "above" or "below" references
        position_ref_pattern = r'(the|see|as shown)\s+(above|below|following|previous)\s+(figure|table|section|image|diagram)'

        for match in re.finditer(position_ref_pattern, content, re.IGNORECASE):
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.INFO,
                description=f"Position-based reference: '{match.group(0)}'",
                suggestion="Consider using explicit labels or links to referenced content"
            ))

        # Pattern for color-only references
        color_ref_pattern = r'(the|see|marked in|shown in|highlighted in)\s+(red|green|blue|yellow|orange|purple|pink)\s+(text|items?|sections?|areas?)?'

        for match in re.finditer(color_ref_pattern, content, re.IGNORECASE):
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.4.1",
                severity=Severity.ERROR,
                description=f"Color-only reference: '{match.group(0)}'",
                suggestion="Add non-color indicator (bold, underline, labels) in addition to color"
            ))

        return content

    def _check_document_structure(self, content: str) -> str:
        """Check for proper document structure in Markdown."""
        lines = content.split('\n')

        # Check if document uses bold/italic as headings instead of proper heading syntax
        potential_heading_issues = []

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Check for bold text at start of line that might be a heading
            if re.match(r'^\*\*[^*]+\*\*\s*$', stripped) or re.match(r'^__[^_]+__\s*$', stripped):
                # This looks like it could be a heading (bold text alone on a line)
                if i + 1 < len(lines) and lines[i + 1].strip() == '':
                    potential_heading_issues.append((i + 1, stripped))

        if potential_heading_issues:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description=f"Document may use bold/italic formatting instead of proper headings ({len(potential_heading_issues)} instances)",
                suggestion="Use # Heading syntax instead of **Bold Text** for section headings"
            ))

        # Check for document title (# at the start)
        has_h1 = any(re.match(r'^#\s+', line) for line in lines)
        if not has_h1:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.2",
                severity=Severity.WARNING,
                description="Document missing top-level heading (# Title)",
                suggestion="Add a # heading at the beginning of the document"
            ))

        return content
