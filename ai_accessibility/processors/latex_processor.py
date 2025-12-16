"""LaTeX accessibility processor."""

import re
from typing import Optional
from .base import BaseProcessor
from utils.accessibility import AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


class LaTeXProcessor(BaseProcessor):
    """Processor for LaTeX documents."""

    # Generic/non-descriptive link texts to fix
    GENERIC_LINK_TEXTS = {
        'here', 'click here', 'click', 'link', 'this link',
        'read more', 'learn more', 'more', 'info', 'details'
    }

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)

    def get_file_extension(self) -> str:
        return ".tex"

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process LaTeX for accessibility.

        Args:
            content: LaTeX content as bytes
            filename: Original filename

        Returns:
            Accessible LaTeX as bytes
        """
        self.reset_report()

        tex_str = content.decode('utf-8', errors='replace')

        # Apply accessibility fixes
        tex_str = self._add_accessibility_packages(tex_str)
        tex_str = self._add_pdf_metadata(tex_str)
        tex_str = self._fix_color_only_information(tex_str)
        tex_str = self._fix_link_text(tex_str)
        tex_str = self._fix_table_accessibility(tex_str)
        tex_str = self._fix_figure_accessibility(tex_str)
        tex_str = self._add_math_descriptions(tex_str)
        tex_str = self._fix_ambiguous_references(tex_str)
        tex_str = self._check_document_structure(tex_str)

        return tex_str.encode('utf-8')

    def _add_accessibility_packages(self, content: str) -> str:
        """Add accessibility-related LaTeX packages."""
        packages_to_add = []

        # Check for accessibility/axessibility package
        if not re.search(r'\\usepackage.*\{accessibility\}', content):
            if not re.search(r'\\usepackage.*\{axessibility\}', content):
                packages_to_add.append('\\usepackage[tagged]{accessibility}')
                self.report.add_fix("Added accessibility package for tagged PDF")

        # Check for hyperref (needed for bookmarks and links)
        if not re.search(r'\\usepackage.*\{hyperref\}', content):
            packages_to_add.append('\\usepackage{hyperref}')
            self.report.add_fix("Added hyperref package")

        if packages_to_add:
            doc_class_match = re.search(r'(\\documentclass.*?\n)', content)
            if doc_class_match:
                insert_pos = doc_class_match.end()
                packages_str = '\n'.join(packages_to_add) + '\n'
                content = content[:insert_pos] + packages_str + content[insert_pos:]
            else:
                content = '\n'.join(packages_to_add) + '\n' + content

        return content

    def _add_pdf_metadata(self, content: str) -> str:
        """Add PDF metadata for accessibility."""
        hypersetup_match = re.search(r'\\hypersetup\s*\{([^}]*)\}', content)

        metadata_options = []

        if not re.search(r'pdflang\s*=', content):
            metadata_options.append('pdflang=en')
            self.report.add_fix("Added pdflang=en for document language")

        if not re.search(r'pdfusetitle', content):
            metadata_options.append('pdfusetitle')

        if not re.search(r'bookmarks\s*=', content):
            metadata_options.append('bookmarks=true')

        if metadata_options:
            if hypersetup_match:
                existing = hypersetup_match.group(1)
                new_options = ',\n  '.join(metadata_options)
                if existing.strip():
                    new_hypersetup = f'\\hypersetup{{{existing},\n  {new_options}}}'
                else:
                    new_hypersetup = f'\\hypersetup{{{new_options}}}'
                content = content.replace(hypersetup_match.group(0), new_hypersetup)
            else:
                options_str = ',\n  '.join(metadata_options)
                hypersetup = f'\\hypersetup{{\n  {options_str}\n}}\n'

                begin_doc = re.search(r'\\begin\{document\}', content)
                if begin_doc:
                    content = content[:begin_doc.start()] + hypersetup + content[begin_doc.start():]
                else:
                    content = hypersetup + content

        return content

    def _fix_color_only_information(self, content: str) -> str:
        """Fix color-only information by adding additional visual indicators."""
        # Pattern for \textcolor{color}{text}
        color_pattern = r'\\textcolor\{(\w+)\}\{([^}]+)\}'

        def fix_color(match):
            color = match.group(1)
            text = match.group(2)
            full_match = match.group(0)

            # Skip if already has additional formatting
            if '\\textbf' in text or '\\textit' in text or '\\underline' in text:
                return full_match

            # Add bold formatting along with color for emphasis
            new_text = f'\\textbf{{\\textcolor{{{color}}}{{{text}}}}}'
            self.report.add_fix(f"Added bold to color-emphasized text: '{text[:30]}...' " if len(text) > 30 else f"Added bold to color-emphasized text: '{text}'")
            return new_text

        return re.sub(color_pattern, fix_color, content)

    def _fix_link_text(self, content: str) -> str:
        """Fix non-descriptive link text."""
        # Pattern for \href{url}{text}
        href_pattern = r'\\href\{([^}]+)\}\{([^}]*)\}'

        def fix_href(match):
            url = match.group(1)
            text = match.group(2).strip()
            full_match = match.group(0)

            # Check if text is generic
            if text.lower() in self.GENERIC_LINK_TEXTS:
                # Generate descriptive text from URL
                try:
                    # Ask Claude to generate descriptive link text
                    descriptive_text = self._generate_link_text(url, text)
                    new_href = f'\\href{{{url}}}{{{descriptive_text}}}'
                    self.report.add_fix(f"Changed link text from '{text}' to '{descriptive_text}'")
                    return new_href
                except Exception as e:
                    # Fallback: use domain name
                    descriptive_text = self._extract_domain_name(url)
                    if descriptive_text and descriptive_text != text:
                        new_href = f'\\href{{{url}}}{{{descriptive_text}}}'
                        self.report.add_fix(f"Changed link text from '{text}' to '{descriptive_text}'")
                        return new_href

            # Check if text is just the URL
            if text == url or not text:
                descriptive_text = self._extract_domain_name(url)
                new_href = f'\\href{{{url}}}{{{descriptive_text}}}'
                self.report.add_fix(f"Added descriptive text for URL link")
                return new_href

            return full_match

        content = re.sub(href_pattern, fix_href, content)

        # Also check for \url{} usage
        url_pattern = r'\\url\{([^}]+)\}'
        url_matches = re.findall(url_pattern, content)

        for url in url_matches:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.4",
                severity=Severity.INFO,
                description=f"URL used as link text: {url[:50]}",
                suggestion="Consider using \\href{url}{descriptive text} instead"
            ))

        return content

    def _generate_link_text(self, url: str, current_text: str) -> str:
        """Generate descriptive link text using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=50,
                system="Generate a short, descriptive link text (2-5 words) for this URL. Return ONLY the link text, nothing else.",
                messages=[{
                    "role": "user",
                    "content": f"URL: {url}\nCurrent unhelpful text: '{current_text}'\nGenerate descriptive link text:"
                }]
            )
            return response.content[0].text.strip()
        except Exception:
            return self._extract_domain_name(url)

    def _extract_domain_name(self, url: str) -> str:
        """Extract a readable name from URL."""
        # Remove protocol
        name = re.sub(r'^https?://', '', url)
        # Remove www
        name = re.sub(r'^www\.', '', name)
        # Get domain
        name = name.split('/')[0]
        # Remove TLD for common domains
        name = re.sub(r'\.(com|org|edu|gov|net|io)$', '', name)
        # Convert to title case and replace dots/dashes
        name = name.replace('.', ' ').replace('-', ' ').replace('_', ' ')
        return name.title() + " Website"

    def _fix_table_accessibility(self, content: str) -> str:
        """Add accessibility features to tables including headers."""
        # Find tabular environments
        tabular_pattern = r'(\\begin\{tabular\}\{[^}]+\})(.*?)(\\end\{tabular\})'

        def fix_tabular(match):
            begin_tabular = match.group(1)
            table_content = match.group(2)
            end_tabular = match.group(3)

            # Check if first row looks like data (numbers) rather than headers
            lines = table_content.strip().split('\n')
            first_data_line = None

            for line in lines:
                line = line.strip()
                if line and not line.startswith('\\hline') and '&' in line:
                    first_data_line = line
                    break

            if first_data_line:
                # Check if first row is numeric (likely data, not headers)
                cells = first_data_line.replace('\\\\', '').split('&')
                cells = [c.strip() for c in cells]

                # If cells look like data (numbers, dates), we need to add headers
                looks_like_data = all(
                    re.match(r'^[\d.,\s°%+-]+$', c) or
                    re.match(r'^\d{4}$', c)  # Year
                    for c in cells if c
                )

                if looks_like_data:
                    # Try to infer headers from context or ask Claude
                    try:
                        headers = self._generate_table_headers(table_content, len(cells))
                        if headers:
                            header_row = ' & '.join(headers) + ' \\\\\n\\hline\n'

                            # Insert header after first \hline or at beginning
                            if '\\hline' in table_content:
                                # Insert after first hline
                                first_hline = table_content.find('\\hline')
                                insert_pos = first_hline + len('\\hline')
                                # Skip newline if present
                                if insert_pos < len(table_content) and table_content[insert_pos] == '\n':
                                    insert_pos += 1
                                new_content = table_content[:insert_pos] + header_row + table_content[insert_pos:]
                            else:
                                new_content = '\n' + header_row + table_content

                            self.report.add_fix(f"Added table headers: {', '.join(headers)}")
                            return begin_tabular + new_content + end_tabular

                    except Exception as e:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.3.1",
                            severity=Severity.ERROR,
                            description="Table appears to be missing header row",
                            suggestion="Add a header row identifying what each column represents"
                        ))

            return match.group(0)

        content = re.sub(tabular_pattern, fix_tabular, content, flags=re.DOTALL)

        # Check table environments for captions and add if missing
        table_env_pattern = r'(\\begin\{table\}.*?)(\\end\{table\})'

        def add_table_caption(match):
            table_content = match.group(1)
            end_table = match.group(2)

            if '\\caption' not in table_content:
                # Generate caption using Claude
                try:
                    caption = self._generate_table_caption(table_content)
                    if caption:
                        # Add caption before \end{table}
                        table_content = table_content.rstrip() + f'\n\\caption{{{caption}}}\n'
                        self.report.add_fix(f"Added table caption: '{caption[:50]}...'")
                except Exception:
                    self.report.add_issue(AccessibilityIssue(
                        wcag_criterion="1.3.1",
                        severity=Severity.WARNING,
                        description="Table environment missing caption",
                        suggestion="Add \\caption{...} to describe the table"
                    ))

            return table_content + end_table

        content = re.sub(table_env_pattern, add_table_caption, content, flags=re.DOTALL)

        return content

    def _generate_table_caption(self, table_content: str) -> str:
        """Generate table caption using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Generate a brief, descriptive table caption (1 sentence). Return ONLY the caption text.",
                messages=[{
                    "role": "user",
                    "content": f"Table content:\n{table_content[:500]}\n\nGenerate a caption:"
                }]
            )
            return response.content[0].text.strip()
        except Exception:
            return None

    def _generate_table_headers(self, table_content: str, num_cols: int) -> list[str]:
        """Generate table headers using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system=f"Based on the table data, suggest {num_cols} column headers. Return ONLY the headers separated by | with no other text.",
                messages=[{
                    "role": "user",
                    "content": f"Table content:\n{table_content[:500]}\n\nSuggest {num_cols} column headers:"
                }]
            )
            headers_text = response.content[0].text.strip()
            headers = [h.strip() for h in headers_text.split('|')]
            if len(headers) == num_cols:
                return headers
        except Exception:
            pass
        return None

    def _fix_figure_accessibility(self, content: str) -> str:
        """Fix figure accessibility including captions and alt text."""
        # Find figure environments
        figure_pattern = r'(\\begin\{figure\}.*?)(\\end\{figure\})'

        def fix_figure(match):
            figure_content = match.group(1)
            end_figure = match.group(2)

            modified = False

            # Check for caption
            if '\\caption' not in figure_content:
                # Find the includegraphics to get context
                img_match = re.search(r'\\includegraphics.*?\{([^}]+)\}', figure_content)
                if img_match:
                    image_name = img_match.group(1)

                    # Generate caption using Claude
                    try:
                        caption = self._generate_figure_caption(image_name, figure_content)
                        if caption:
                            # Add caption before \end{figure}
                            figure_content = figure_content.rstrip() + f'\n\\caption{{{caption}}}\n'
                            self.report.add_fix(f"Added caption for figure: {image_name}")
                            modified = True
                    except Exception as e:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.1.1",
                            severity=Severity.ERROR,
                            description=f"Figure missing caption: {image_name}",
                            suggestion="Add \\caption{...} describing the figure"
                        ))

            # Check for alt text (pdftooltip)
            if '\\includegraphics' in figure_content and '\\pdftooltip' not in figure_content:
                # Add pdftooltip for alt text
                img_pattern = r'(\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\})'
                img_match = re.search(img_pattern, figure_content)
                if img_match:
                    full_img = img_match.group(1)
                    image_name = img_match.group(2)

                    try:
                        alt_text = self.claude_client.generate_alt_text(
                            image_context=f"LaTeX figure: {image_name}"
                        )
                        if alt_text and alt_text != "DECORATIVE":
                            new_img = f'\\pdftooltip{{{full_img}}}{{{alt_text}}}'
                            figure_content = figure_content.replace(full_img, new_img)
                            self.report.add_fix(f"Added alt text for figure: {image_name}")
                            modified = True
                    except Exception:
                        pass

            return figure_content + end_figure

        content = re.sub(figure_pattern, fix_figure, content, flags=re.DOTALL)

        # Also handle standalone includegraphics (not in figure environment)
        standalone_pattern = r'(?<!\\pdftooltip\{)(\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\})'

        def fix_standalone_image(match):
            full_match = match.group(1)
            image_name = match.group(2)

            # Check if already wrapped or in figure environment
            try:
                alt_text = self.claude_client.generate_alt_text(
                    image_context=f"LaTeX image: {image_name}"
                )
                if alt_text and alt_text != "DECORATIVE":
                    self.report.add_fix(f"Added alt text for standalone image: {image_name}")
                    return f'\\pdftooltip{{{full_match}}}{{{alt_text}}}'
            except Exception:
                pass

            return full_match

        # Only apply to images not already in pdftooltip
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if '\\includegraphics' in line and '\\pdftooltip' not in line:
                if '\\begin{figure}' not in content[max(0, content.find(line)-200):content.find(line)]:
                    line = re.sub(standalone_pattern, fix_standalone_image, line)
            new_lines.append(line)
        content = '\n'.join(new_lines)

        return content

    def _generate_figure_caption(self, image_name: str, context: str) -> str:
        """Generate figure caption using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Generate a brief, descriptive figure caption (1 sentence). Return ONLY the caption text.",
                messages=[{
                    "role": "user",
                    "content": f"Figure filename: {image_name}\nContext: {context[:300]}\n\nGenerate a caption:"
                }]
            )
            return response.content[0].text.strip()
        except Exception:
            return None

    def _add_math_descriptions(self, content: str) -> str:
        """Add descriptions for mathematical equations."""
        # Pattern for display math ($$...$$, \[...\], equation environment)
        display_math_patterns = [
            (r'\$\$([^$]+)\$\$', 'display'),
            (r'\\\[([^\]]+)\\\]', 'display'),
            (r'\\begin\{equation\}(.*?)\\end\{equation\}', 'equation'),
        ]

        for pattern, math_type in display_math_patterns:
            matches = list(re.finditer(pattern, content, re.DOTALL))

            for match in reversed(matches):  # Reverse to preserve positions
                math_content = match.group(1).strip()
                full_match = match.group(0)

                # Check if there's descriptive text before the equation
                preceding_text = content[max(0, match.start()-200):match.start()]

                # Look for description keywords
                has_description = any(keyword in preceding_text.lower() for keyword in [
                    'where', 'equation', 'formula', 'defined as', 'given by',
                    'expressed as', 'calculated', 'represents'
                ])

                if not has_description:
                    # Generate description using Claude
                    try:
                        description = self._generate_math_description(math_content)
                        if description:
                            # Add description before the equation
                            desc_text = f"\n% Equation description for accessibility: {description}\n"
                            # Also add as actual text if it's helpful
                            readable_desc = f"The following equation {description}:\n"
                            content = content[:match.start()] + readable_desc + content[match.start():]
                            self.report.add_fix(f"Added description for equation")
                    except Exception:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.1.1",
                            severity=Severity.WARNING,
                            description="Mathematical equation lacks textual description",
                            suggestion="Add context explaining what the equation represents"
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
                    "content": f"LaTeX equation: {math_content}\n\nDescribe what this represents:"
                }]
            )
            return response.content[0].text.strip().lower()
        except Exception:
            return None

    def _fix_ambiguous_references(self, content: str) -> str:
        """Fix ambiguous page/visual-only references."""
        # Pattern for "see page X" or "on page X"
        page_ref_pattern = r'([Ss]ee|[Oo]n|[Rr]efer to)\s+page\s+(\d+)'

        matches = list(re.finditer(page_ref_pattern, content))

        for match in matches:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description=f"Ambiguous page reference: '{match.group(0)}'",
                suggestion="Use LaTeX cross-referencing (\\ref, \\pageref) or provide section name"
            ))

        # Pattern for "above" or "below" references
        position_ref_pattern = r'(the|see|as shown)\s+(above|below|following|previous)\s+(figure|table|section|equation)'

        for match in re.finditer(position_ref_pattern, content, re.IGNORECASE):
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.INFO,
                description=f"Position-based reference: '{match.group(0)}'",
                suggestion="Consider using \\ref{label} for explicit references"
            ))

        return content

    def _check_document_structure(self, content: str) -> str:
        """Check for proper document structure."""
        has_section = bool(re.search(r'\\(section|chapter|part)\{', content))
        has_formatting_only = bool(re.search(r'\\(textbf|Large|huge)\{[^}]*\}\s*\\\\', content))

        if has_formatting_only and not has_section:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description="Document may use formatting instead of proper sectioning",
                suggestion="Use \\section{}, \\subsection{} instead of bold/large text for headings"
            ))

        has_title = bool(re.search(r'\\title\{', content))

        if not has_title:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.2",
                severity=Severity.WARNING,
                description="Document missing \\title{}",
                suggestion="Add \\title{Document Title} for proper document structure"
            ))

        return content
