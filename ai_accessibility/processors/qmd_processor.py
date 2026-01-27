"""Quarto Markdown (QMD) accessibility processor.

Integrates validation logic from quarto-wcag-compliance skill scripts:
- check_quarto_accessibility.py - Pre-render validation
- validate_contrast.py - Color contrast checking
- fix_unicode.py - pdflatex Unicode compatibility
"""

import re
import math
from typing import Optional, Dict, List, Tuple
from .markdown_processor import MarkdownProcessor
from utils.accessibility import AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


# Unicode to ASCII replacements for pdflatex compatibility
# From quarto-wcag-compliance skill: fix_unicode.py
UNICODE_REPLACEMENTS: Dict[str, str] = {
    # Checkmarks and crosses
    '❌': 'X', '✗': 'X', '✘': 'X',
    '✓': '+', '✔': '+',
    '☑': '[X]', '☐': '[ ]',
    # Colored circles/indicators
    '🟢': '[OK]', '🟡': '[WARN]', '🔴': '[ERROR]',
    '⚫': '[*]', '⚪': '[ ]', '🔵': '[INFO]',
    # Warning/info symbols
    '⚠️': 'WARNING:', '⚠': 'WARNING:',
    '❗': '!', '❓': '?',
    '💡': 'TIP:', '📝': 'NOTE:', '📌': '*',
    'ℹ️': 'INFO:', 'ℹ': 'INFO:',
    # Arrows
    '→': '->', '←': '<-', '↔': '<->',
    '⇒': '=>', '⇐': '<=', '⇔': '<=>',
    '↑': '^', '↓': 'v',
    # Smart quotes (common from Word/Google Docs)
    '"': '"', '"': '"', ''': "'", ''': "'",
    '«': '"', '»': '"',
    # Dashes
    '—': '--', '–': '-', '−': '-',
    # Other punctuation
    '…': '...', '•': '*', '·': '*',
    '○': 'o', '●': '*', '◦': 'o',
    '◆': '*', '◇': 'o', '★': '*', '☆': '*',
    # Math symbols
    '×': 'x', '÷': '/', '±': '+/-',
    '≤': '<=', '≥': '>=', '≠': '!=', '≈': '~=',
    '∞': 'infinity',
    # Misc
    '©': '(c)', '®': '(R)', '™': '(TM)',
    '°': ' degrees', '€': 'EUR', '£': 'GBP', '¥': 'JPY',
}

# Named colors to hex mapping for contrast checking
# From quarto-wcag-compliance skill: validate_contrast.py
NAMED_COLORS: Dict[str, str] = {
    'black': '#000000', 'white': '#FFFFFF',
    'red': '#FF0000', 'green': '#008000', 'blue': '#0000FF',
    'navy': '#000080', 'gray': '#808080', 'grey': '#808080',
    'silver': '#C0C0C0', 'maroon': '#800000', 'purple': '#800080',
    'fuchsia': '#FF00FF', 'lime': '#00FF00', 'olive': '#808000',
    'yellow': '#FFFF00', 'aqua': '#00FFFF', 'teal': '#008080',
    'orange': '#FFA500',
}


class QMDProcessor(MarkdownProcessor):
    """Processor for Quarto Markdown documents.

    Integrates validation from quarto-wcag-compliance skill for comprehensive
    WCAG 2.1 AA accessibility checking and fixing.
    """

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)

    def get_file_extension(self) -> str:
        return ".qmd"

    # =========================================================================
    # Color Contrast Utilities (from validate_contrast.py)
    # =========================================================================

    def _hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join([c * 2 for c in hex_color])
        if len(hex_color) != 6:
            raise ValueError(f"Invalid hex color: #{hex_color}")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _relative_luminance(self, rgb: Tuple[int, int, int]) -> float:
        """Calculate relative luminance per WCAG 2.1."""
        def channel_luminance(value: int) -> float:
            srgb = value / 255
            if srgb <= 0.03928:
                return srgb / 12.92
            return math.pow((srgb + 0.055) / 1.055, 2.4)
        r, g, b = rgb
        return 0.2126 * channel_luminance(r) + 0.7152 * channel_luminance(g) + 0.0722 * channel_luminance(b)

    def _contrast_ratio(self, color1: str, color2: str) -> float:
        """Calculate contrast ratio between two colors."""
        rgb1 = self._hex_to_rgb(color1)
        rgb2 = self._hex_to_rgb(color2)
        lum1 = self._relative_luminance(rgb1)
        lum2 = self._relative_luminance(rgb2)
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)
        return (lighter + 0.05) / (darker + 0.05)

    def _normalize_color(self, color: str) -> str:
        """Normalize color to hex format."""
        color = color.lower().strip()
        if color in NAMED_COLORS:
            return NAMED_COLORS[color]
        if color.startswith('#'):
            return color.upper()
        return color

    def _check_color_contrast(self, content: str) -> None:
        """Check color definitions in QMD for WCAG AA contrast compliance."""
        # Patterns for colors in YAML/CSS
        color_patterns = [
            (r'color:\s*["\']?(#[0-9A-Fa-f]{3,6})["\']?', 'text color'),
            (r'linkcolor:\s*([a-zA-Z]+|#[0-9A-Fa-f]{3,6})', 'link color'),
            (r'urlcolor:\s*([a-zA-Z]+|#[0-9A-Fa-f]{3,6})', 'URL color'),
            (r'background(?:-color)?:\s*(#[0-9A-Fa-f]{3,6})', 'background'),
        ]

        lines = content.split('\n')
        for i, line in enumerate(lines):
            for pattern, color_type in color_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    color = self._normalize_color(match.group(1))
                    if color.startswith('#'):
                        # Check against white background (common default)
                        try:
                            ratio = self._contrast_ratio(color, '#FFFFFF')
                            if ratio < 4.5:
                                self.report.add_issue(AccessibilityIssue(
                                    wcag_criterion="1.4.3",
                                    severity=Severity.WARNING,
                                    description=f"Low contrast {color_type}: {color} has {ratio:.1f}:1 ratio (need 4.5:1)",
                                    location=f"Line {i+1}"
                                ))
                        except ValueError:
                            pass  # Skip invalid colors

    # =========================================================================
    # Unicode Fixing (from fix_unicode.py)
    # =========================================================================

    def _find_unicode_chars(self, content: str) -> List[Tuple[str, int, str]]:
        """Find non-ASCII characters that may cause pdflatex errors."""
        issues = []
        lines = content.split('\n')
        for line_num, line in enumerate(lines, 1):
            for i, char in enumerate(line):
                if ord(char) > 127 and char in UNICODE_REPLACEMENTS:
                    start = max(0, i - 10)
                    end = min(len(line), i + 11)
                    context = line[start:end]
                    issues.append((char, line_num, context))
        return issues

    def _fix_unicode_chars(self, content: str) -> str:
        """Replace Unicode characters with ASCII equivalents for pdflatex."""
        count = 0
        for old, new in UNICODE_REPLACEMENTS.items():
            if old in content:
                occurrences = content.count(old)
                content = content.replace(old, new)
                count += occurrences

        if count > 0:
            self.report.add_fix(f"Replaced {count} Unicode character(s) with ASCII equivalents for PDF compatibility")

        return content

    def _check_unicode_warnings(self, content: str) -> None:
        """Check for Unicode characters that may cause pdflatex errors (warning only)."""
        problematic_chars = {
            '\u274c': 'cross mark',       # ❌
            '\u2713': 'checkmark',        # ✓
            '\u2714': 'checkmark',        # ✔
            '\U0001F7E2': 'green circle', # 🟢
            '\U0001F7E1': 'yellow circle',# 🟡
            '\U0001F534': 'red circle',   # 🔴
            '\u26a0': 'warning sign',     # ⚠
            '\U0001F4A1': 'lightbulb',    # 💡
            '\U0001F4DD': 'memo',         # 📝
            '\u201c': 'left smart quote', # "
            '\u201d': 'right smart quote',# "
            '\u2018': 'left smart quote', # '
            '\u2019': 'right smart quote',# '
            '\u2014': 'em dash',          # —
            '\u2013': 'en dash',          # –
            '\u2026': 'ellipsis',         # …
        }

        found_chars = set()
        lines = content.split('\n')
        for i, line in enumerate(lines):
            for char, name in problematic_chars.items():
                if char in line and char not in found_chars:
                    found_chars.add(char)
                    self.report.add_issue(AccessibilityIssue(
                        wcag_criterion="PDF-COMPAT",
                        severity=Severity.INFO,
                        description=f"Unicode '{char}' ({name}) replaced for pdflatex compatibility",
                        location=f"Line {i+1}"
                    ))

    # =========================================================================
    # Enhanced Link Text Checking (from check_quarto_accessibility.py)
    # =========================================================================

    def _check_link_text_quality(self, content: str) -> None:
        """Enhanced link text validation from skill."""
        lines = content.split('\n')
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

        bad_link_texts = [
            'click here', 'here', 'read more', 'more', 'link',
            'this', 'this link', 'learn more', 'details'
        ]

        for i, line in enumerate(lines):
            for match in link_pattern.finditer(line):
                link_text = match.group(1).strip().lower()
                if link_text in bad_link_texts:
                    self.report.add_issue(AccessibilityIssue(
                        wcag_criterion="2.4.4",
                        severity=Severity.ERROR,
                        description=f"Non-descriptive link text: '{match.group(1)}'",
                        location=f"Line {i+1}",
                        suggestion="Use descriptive text that indicates the link destination"
                    ))
                elif len(link_text) < 3:
                    self.report.add_issue(AccessibilityIssue(
                        wcag_criterion="2.4.4",
                        severity=Severity.WARNING,
                        description=f"Link text may be too brief: '{match.group(1)}'",
                        location=f"Line {i+1}"
                    ))

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process QMD for WCAG 2.1 AA accessibility.

        Integrates validation logic from quarto-wcag-compliance skill:
        - Unicode fixing for pdflatex compatibility
        - Color contrast validation (WCAG 1.4.3)
        - Enhanced link text checking (WCAG 2.4.4)

        Args:
            content: QMD content as bytes
            filename: Original filename

        Returns:
            Accessible QMD as bytes
        """
        self.reset_report()

        qmd_str = content.decode('utf-8', errors='replace')

        # =====================================================================
        # Pre-processing: Fix Unicode for pdflatex compatibility
        # (from quarto-wcag-compliance skill: fix_unicode.py)
        # =====================================================================
        self._check_unicode_warnings(qmd_str)  # Report what will be fixed
        qmd_str = self._fix_unicode_chars(qmd_str)

        # Separate YAML frontmatter
        frontmatter, body = self._split_frontmatter(qmd_str)

        # Process YAML frontmatter for accessibility
        if frontmatter:
            frontmatter = self._process_frontmatter(frontmatter)

        # =====================================================================
        # Validation: Color contrast checking
        # (from quarto-wcag-compliance skill: validate_contrast.py)
        # =====================================================================
        if frontmatter:
            self._check_color_contrast(frontmatter)

        # =====================================================================
        # Apply standard Markdown fixes to body
        # =====================================================================
        body = self._fix_heading_hierarchy(body)
        body = self._add_image_alt_text(body)
        body = self._fix_link_text(body)
        # Note: Don't apply _add_code_language for QMD - Quarto uses {r}, {python}, etc.
        body = self._add_table_headers(body)
        body = self._fix_color_only_information(body)
        body = self._add_math_descriptions(body)
        body = self._fix_ambiguous_references(body)
        body = self._check_document_structure(body)

        # =====================================================================
        # Enhanced validation: Link text quality
        # (from quarto-wcag-compliance skill: check_quarto_accessibility.py)
        # =====================================================================
        self._check_link_text_quality(body)

        # Apply QMD-specific code chunk accessibility
        body = self._fix_quarto_code_chunks(body)

        # Apply Quarto-specific fixes
        body = self._fix_quarto_figures(body)
        body = self._fix_quarto_callouts(body)
        body = self._fix_quarto_tabsets(body)
        body = self._add_div_accessibility(body)

        # Recombine
        if frontmatter:
            result = f"---\n{frontmatter}---\n{body}"
        else:
            result = body

        return result.encode('utf-8')

    def _split_frontmatter(self, content: str) -> tuple[str, str]:
        """Split YAML frontmatter from body."""
        if not content.startswith('---'):
            return '', content

        # Find the closing ---
        match = re.match(r'^---\n(.*?)\n---\n?(.*)', content, re.DOTALL)
        if match:
            return match.group(1), match.group(2)

        return '', content

    def _process_frontmatter(self, frontmatter: str) -> str:
        """Add accessibility-related YAML options."""
        lines = frontmatter.split('\n')

        # Remove any trailing empty lines
        while lines and not lines[-1].strip():
            lines.pop()

        # Check for lang
        has_lang = any(line.strip().startswith('lang:') for line in lines)
        if not has_lang:
            lines.append('lang: en')
            self.report.add_fix("Added lang: en to frontmatter")

        # Check for title
        has_title = any(line.strip().startswith('title:') for line in lines)
        if not has_title:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.2",
                severity=Severity.WARNING,
                description="Document missing title in frontmatter"
            ))

        # Check for accessibility format options
        format_lines = []
        in_format = False
        format_indent = 0

        for i, line in enumerate(lines):
            if re.match(r'^format:', line):
                in_format = True
                format_indent = len(line) - len(line.lstrip())
            elif in_format:
                current_indent = len(line) - len(line.lstrip())
                if line.strip() and current_indent <= format_indent:
                    in_format = False

        # Add accessibility hints for HTML format
        if any('html:' in line for line in lines):
            # Check for toc-title
            if not any('toc-title:' in line for line in lines):
                # Add toc-title for screen readers
                for i, line in enumerate(lines):
                    if 'toc:' in line and 'true' in line.lower():
                        lines.insert(i + 1, '  toc-title: "Table of Contents"')
                        self.report.add_fix("Added toc-title for accessibility")
                        break

        # Return with trailing newline to ensure proper YAML closure
        return '\n'.join(lines) + '\n'

    def _fix_quarto_code_chunks(self, content: str) -> str:
        """Add accessibility options to Quarto code chunks that generate figures or tables."""
        # Pattern for Quarto code chunks: ```{r} or ```{python} etc.
        chunk_pattern = r'(```\{(\w+)([^}]*)\}\n)(.*?)(```)'

        def fix_chunk(match):
            opening = match.group(1)
            language = match.group(2)
            chunk_options = match.group(3)
            chunk_body = match.group(4)
            closing = match.group(5)

            # Check if this chunk generates a figure (has ggplot, plot, etc.)
            generates_figure = any(fig_keyword in chunk_body for fig_keyword in [
                'ggplot', 'plot(', 'geom_', 'plt.', 'matplotlib', 'seaborn',
                'fig,', 'ax.', 'figure(', 'chart'
            ])

            # Check if this chunk generates a table (has kable, gt, datatable, etc.)
            generates_table = any(tbl_keyword in chunk_body for tbl_keyword in [
                'kable', 'knitr::kable', 'gt(', 'DT::datatable', 'reactable',
                'print(data', 'head(', 'summary('
            ])

            # Parse existing chunk options
            existing_options = set()
            lines = chunk_body.split('\n')
            first_code_line_idx = 0

            for i, line in enumerate(lines):
                if line.strip().startswith('#|'):
                    option_match = re.match(r'#\|\s*(\w+[-\w]*)\s*:', line)
                    if option_match:
                        existing_options.add(option_match.group(1))
                    first_code_line_idx = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    # Found first non-option, non-comment line
                    first_code_line_idx = i
                    break

            new_options = []

            # Add figure accessibility options if needed
            if generates_figure:
                if 'fig-cap' not in existing_options:
                    # Generate caption using Claude
                    try:
                        caption = self._generate_figure_caption(chunk_body)
                        if caption:
                            new_options.append(f'#| fig-cap: "{caption}"')
                            self.report.add_fix(f"Added fig-cap to code chunk")
                    except Exception:
                        new_options.append('#| fig-cap: "Figure description needed"')
                        self.report.add_fix("Added placeholder fig-cap to code chunk")

                if 'fig-alt' not in existing_options:
                    # Generate alt text using Claude
                    try:
                        alt_text = self._generate_figure_alt(chunk_body)
                        if alt_text:
                            new_options.append(f'#| fig-alt: "{alt_text}"')
                            self.report.add_fix(f"Added fig-alt to code chunk")
                    except Exception:
                        new_options.append('#| fig-alt: "Alternative text for figure needed"')
                        self.report.add_fix("Added placeholder fig-alt to code chunk")

            # Add table accessibility options if needed
            if generates_table:
                if 'tbl-cap' not in existing_options:
                    # Generate table caption using Claude
                    try:
                        caption = self._generate_table_caption(chunk_body)
                        if caption:
                            new_options.append(f'#| tbl-cap: "{caption}"')
                            self.report.add_fix(f"Added tbl-cap to code chunk")
                    except Exception:
                        new_options.append('#| tbl-cap: "Table description needed"')
                        self.report.add_fix("Added placeholder tbl-cap to code chunk")

            # Insert new options after existing options
            if new_options:
                # Find where to insert (after existing #| options)
                insert_idx = first_code_line_idx
                for i, line in enumerate(lines):
                    if line.strip().startswith('#|'):
                        insert_idx = i + 1

                # Insert new options
                for opt in reversed(new_options):
                    lines.insert(insert_idx, opt)

                chunk_body = '\n'.join(lines)

            return opening + chunk_body + closing

        return re.sub(chunk_pattern, fix_chunk, content, flags=re.DOTALL)

    def _generate_figure_caption(self, code: str) -> str:
        """Generate figure caption using Claude based on code."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Generate a brief, descriptive figure caption (1 sentence) based on this plotting code. Return ONLY the caption text, no quotes.",
                messages=[{
                    "role": "user",
                    "content": f"Code:\n{code[:500]}\n\nGenerate a caption:"
                }]
            )
            return response.content[0].text.strip().replace('"', "'")
        except Exception:
            return None

    def _generate_figure_alt(self, code: str) -> str:
        """Generate figure alt text using Claude based on code."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=150,
                system="Generate alt text for a figure based on this plotting code. Describe the type of chart and what data it shows. Return ONLY the alt text, no quotes.",
                messages=[{
                    "role": "user",
                    "content": f"Code:\n{code[:500]}\n\nGenerate alt text:"
                }]
            )
            return response.content[0].text.strip().replace('"', "'")
        except Exception:
            return None

    def _generate_table_caption(self, code: str) -> str:
        """Generate table caption using Claude based on code."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Generate a brief, descriptive table caption (1 sentence) based on this code that creates a table. Return ONLY the caption text, no quotes.",
                messages=[{
                    "role": "user",
                    "content": f"Code:\n{code[:500]}\n\nGenerate a table caption:"
                }]
            )
            return response.content[0].text.strip().replace('"', "'")
        except Exception:
            return None

    def _fix_quarto_figures(self, content: str) -> str:
        """Fix accessibility for Quarto figure syntax."""
        # Quarto figure syntax: ![Caption](image.png){#fig-id fig-alt="alt text"}

        # Pattern for figures without fig-alt
        fig_pattern = r'!\[([^\]]*)\]\(([^)]+)\)(\{[^}]*\})?'

        def fix_figure(match):
            caption = match.group(1)
            src = match.group(2)
            attrs = match.group(3) or ''

            # Check if fig-alt exists
            if 'fig-alt=' in attrs:
                return match.group(0)

            # Need to add fig-alt
            if not caption.strip():
                # Generate alt text
                try:
                    alt_text = self.claude_client.generate_alt_text(
                        image_context=f"Quarto figure with source: {src}"
                    )

                    if alt_text == "DECORATIVE":
                        alt_text = ""
                    else:
                        self.report.add_fix(f"Added fig-alt for: {src[:50]}")

                except Exception as e:
                    alt_text = ""
                    self.report.add_warning(f"Could not generate alt text: {str(e)}")
            else:
                # Use caption as alt if no alt exists
                alt_text = caption

            # Add fig-alt to attributes
            if attrs:
                # Insert fig-alt into existing braces
                new_attrs = attrs[:-1] + f' fig-alt="{alt_text}"' + '}'
            else:
                new_attrs = '{' + f'fig-alt="{alt_text}"' + '}'

            return f'![{caption}]({src}){new_attrs}'

        return re.sub(fig_pattern, fix_figure, content)

    def _fix_quarto_callouts(self, content: str) -> str:
        """Ensure Quarto callouts have accessible titles."""
        # Callout syntax: ::: {.callout-note}
        # ## Title
        # Content
        # :::

        callout_pattern = r':::\s*\{\.callout-(\w+)([^}]*)\}\s*\n(.*?):::'

        def fix_callout(match):
            callout_type = match.group(1)
            attrs = match.group(2)
            content_block = match.group(3)

            # Check if there's a title attribute or heading
            has_title = 'title=' in attrs or re.match(r'^\s*##', content_block)

            if not has_title:
                # Add default accessible title based on callout type
                titles = {
                    'note': 'Note',
                    'warning': 'Warning',
                    'tip': 'Tip',
                    'important': 'Important',
                    'caution': 'Caution'
                }
                default_title = titles.get(callout_type, callout_type.capitalize())
                new_attrs = f'{attrs} title="{default_title}"'
                self.report.add_fix(f"Added title to {callout_type} callout")

                return f'::: {{.callout-{callout_type}{new_attrs}}}\n{content_block}:::'

            return match.group(0)

        return re.sub(callout_pattern, fix_callout, content, flags=re.DOTALL)

    def _fix_quarto_tabsets(self, content: str) -> str:
        """Ensure Quarto tabsets are accessible."""
        # Tabset syntax: ::: {.panel-tabset}
        # ## Tab 1
        # Content
        # ## Tab 2
        # Content
        # :::

        tabset_pattern = r':::\s*\{\.panel-tabset([^}]*)\}\s*\n(.*?):::'

        def fix_tabset(match):
            attrs = match.group(1)
            content_block = match.group(2)

            # Check for group attribute (for keyboard navigation)
            if 'group=' not in attrs:
                # Add group for linked tabsets
                new_attrs = f'{attrs} group="default-tabset"'
                self.report.add_fix("Added group attribute to tabset for keyboard navigation")
                return f'::: {{.panel-tabset{new_attrs}}}\n{content_block}:::'

            return match.group(0)

        return re.sub(tabset_pattern, fix_tabset, content, flags=re.DOTALL)

    def _add_div_accessibility(self, content: str) -> str:
        """Add accessibility attributes to Quarto divs."""
        # Generic div syntax: ::: {#id .class}

        # Pattern for divs that might need roles
        div_pattern = r':::\s*\{([^}]+)\}'

        lines = content.split('\n')
        modified = False

        for i, line in enumerate(lines):
            match = re.match(div_pattern, line)
            if match:
                attrs = match.group(1)

                # Check for content that might need landmarks
                if '.sidebar' in attrs and 'role=' not in attrs:
                    lines[i] = line.replace(attrs, f'{attrs} role="complementary"')
                    self.report.add_fix("Added role='complementary' to sidebar div")
                    modified = True

                elif '.footer' in attrs and 'role=' not in attrs:
                    lines[i] = line.replace(attrs, f'{attrs} role="contentinfo"')
                    self.report.add_fix("Added role='contentinfo' to footer div")
                    modified = True

                elif '.nav' in attrs and 'role=' not in attrs:
                    lines[i] = line.replace(attrs, f'{attrs} role="navigation"')
                    self.report.add_fix("Added role='navigation' to nav div")
                    modified = True

        if modified:
            return '\n'.join(lines)
        return content
