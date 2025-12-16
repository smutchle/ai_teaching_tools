"""HTML accessibility processor."""

import re
from typing import Optional
from bs4 import BeautifulSoup, Tag
from .base import BaseProcessor
from utils.accessibility import AccessibilityChecker, AccessibilityIssue, Severity
from utils.claude_client import ClaudeClient


class HTMLProcessor(BaseProcessor):
    """Processor for HTML documents."""

    # Generic/non-descriptive link texts to fix
    GENERIC_LINK_TEXTS = {
        'here', 'click here', 'click', 'link', 'this link',
        'read more', 'learn more', 'more', 'info', 'details'
    }

    def __init__(self, claude_client: Optional[ClaudeClient] = None):
        super().__init__(claude_client)

    def get_file_extension(self) -> str:
        return ".html"

    def process(self, content: bytes, filename: str = "") -> bytes:
        """
        Process HTML for WCAG 2.1 AA accessibility.

        Args:
            content: HTML content as bytes
            filename: Original filename

        Returns:
            Accessible HTML as bytes
        """
        self.reset_report()

        # Parse HTML
        html_str = content.decode('utf-8', errors='replace')
        soup = BeautifulSoup(html_str, 'html.parser')

        # Apply accessibility fixes
        self._add_lang_attribute(soup)
        self._add_document_title(soup, filename)
        self._fix_heading_hierarchy(soup)
        self._add_image_alt_text(soup)
        self._fix_table_accessibility(soup)
        self._fix_link_text(soup)
        self._add_skip_link(soup)
        self._fix_form_labels(soup)
        self._add_aria_landmarks(soup)
        self._fix_color_only_information(soup)
        self._add_math_descriptions(soup)
        self._fix_ambiguous_references(soup)
        self._check_document_structure(soup)

        # Return processed HTML
        return str(soup).encode('utf-8')

    def _add_lang_attribute(self, soup: BeautifulSoup):
        """Add lang attribute to html element if missing."""
        html_tag = soup.find('html')
        if html_tag and isinstance(html_tag, Tag):
            if not html_tag.get('lang'):
                html_tag['lang'] = 'en'
                self.report.add_fix("Added lang='en' attribute to <html> element")
        else:
            # No html tag, might be a fragment - wrap it
            self.report.add_warning("Document has no <html> element - may be a fragment")

    def _add_document_title(self, soup: BeautifulSoup, filename: str):
        """Ensure document has a title."""
        head = soup.find('head')
        if head and isinstance(head, Tag):
            title = head.find('title')
            if not title:
                new_title = soup.new_tag('title')
                # Use filename without extension as title
                title_text = filename.rsplit('.', 1)[0] if filename else "Document"
                new_title.string = title_text
                head.insert(0, new_title)
                self.report.add_fix(f"Added document title: '{title_text}'")
            elif title and (not title.string or not title.string.strip()):
                title.string = filename.rsplit('.', 1)[0] if filename else "Document"
                self.report.add_fix("Added text to empty title element")

    def _fix_heading_hierarchy(self, soup: BeautifulSoup):
        """Fix heading hierarchy to not skip levels."""
        headings = []
        for tag in soup.find_all(re.compile(r'^h[1-6]$')):
            level = int(tag.name[1])
            headings.append((level, tag.get_text(strip=True), tag))

        if not headings:
            return

        # Check and fix hierarchy
        issues = AccessibilityChecker.check_heading_hierarchy(
            [(level, text) for level, text, _ in headings]
        )

        for issue in issues:
            issue.auto_fixed = False  # We'll mark as fixed if we fix it
            self.report.add_issue(issue)

        # Get suggested fixes
        heading_data = [(level, text) for level, text, _ in headings]
        fixes = AccessibilityChecker.fix_heading_hierarchy(heading_data)

        # Apply fixes
        for i, (orig_level, text, suggested) in enumerate(fixes):
            if orig_level != suggested:
                tag = headings[i][2]
                tag.name = f'h{suggested}'
                self.report.add_fix(f"Changed h{orig_level} to h{suggested}: '{text[:30]}...' " if len(text) > 30 else f"Changed h{orig_level} to h{suggested}: '{text}'")

    def _add_image_alt_text(self, soup: BeautifulSoup):
        """Add alt text to images missing it."""
        images = soup.find_all('img')

        for img in images:
            if not isinstance(img, Tag):
                continue

            alt = img.get('alt')

            # Check existing alt text
            issue = AccessibilityChecker.check_image_alt(alt)
            if issue:
                self.report.add_issue(issue)

            # If no alt or problematic alt, generate new one
            if alt is None or (isinstance(alt, str) and self._is_unhelpful_alt(alt)):
                # Get context
                src = img.get('src', '')
                context = f"Image source: {src}"

                # Get surrounding text
                parent = img.parent
                surrounding = ""
                if parent:
                    surrounding = parent.get_text(strip=True)[:200]

                # Generate alt text with Claude
                try:
                    new_alt = self.claude_client.generate_alt_text(
                        image_context=context,
                        surrounding_text=surrounding
                    )

                    if new_alt == "DECORATIVE":
                        img['alt'] = ""
                        img['role'] = "presentation"
                        self.report.add_fix(f"Marked image as decorative: {src[:50]}")
                    else:
                        img['alt'] = new_alt
                        self.report.add_fix(f"Added alt text for image: {src[:50]}")
                except Exception as e:
                    img['alt'] = ""
                    self.report.add_warning(f"Could not generate alt text for {src[:50]}: {str(e)}")

    def _is_unhelpful_alt(self, alt: str) -> bool:
        """Check if alt text is unhelpful."""
        if not alt:
            return True
        normalized = alt.lower().strip()
        unhelpful = ['image', 'img', 'picture', 'photo', 'graphic', 'untitled']
        return normalized in unhelpful or re.match(r'^(img_|dsc|image)\d*$', normalized)

    def _fix_table_accessibility(self, soup: BeautifulSoup):
        """Add accessibility features to tables."""
        tables = soup.find_all('table')

        for table in tables:
            if not isinstance(table, Tag):
                continue

            # Check for headers
            headers = table.find_all('th')
            has_headers = len(headers) > 0

            # Check for caption
            caption = table.find('caption')
            has_caption = caption is not None

            # Check for scope
            has_scope = any(th.get('scope') for th in headers if isinstance(th, Tag))

            # Report issues
            issues = AccessibilityChecker.check_table_accessibility(has_headers, has_caption, has_scope)
            for issue in issues:
                self.report.add_issue(issue)

            # Add scope to headers if missing
            for th in headers:
                if isinstance(th, Tag) and not th.get('scope'):
                    # Determine if row or column header
                    parent = th.parent
                    if parent and parent.name == 'tr':
                        # If in first row, likely column header
                        if parent == table.find('tr') or parent.parent.name == 'thead':
                            th['scope'] = 'col'
                        else:
                            th['scope'] = 'row'
                    self.report.add_fix("Added scope attribute to table header")

            # Generate caption if missing
            if not has_caption:
                try:
                    # Get table content for context
                    table_text = table.get_text(strip=True)[:500]
                    caption_text = self.claude_client.generate_table_caption(table_text)

                    new_caption = soup.new_tag('caption')
                    new_caption.string = caption_text
                    table.insert(0, new_caption)
                    self.report.add_fix(f"Added table caption: '{caption_text[:50]}...'")
                except Exception as e:
                    self.report.add_warning(f"Could not generate table caption: {str(e)}")

    def _fix_link_text(self, soup: BeautifulSoup):
        """Fix non-descriptive link text."""
        links = soup.find_all('a')
        links_to_check = []

        for link in links:
            if not isinstance(link, Tag):
                continue

            text = link.get_text(strip=True)
            href = link.get('href', '')

            if text:
                issue = AccessibilityChecker.check_link_text(text, href)
                if issue:
                    self.report.add_issue(issue)
                    links_to_check.append({'text': text, 'href': href, 'element': link})

        # Use Claude to improve link texts
        if links_to_check:
            try:
                improvements = self.claude_client.improve_link_text(
                    [{'text': l['text'], 'href': l['href']} for l in links_to_check]
                )

                for i, improvement in enumerate(improvements):
                    if improvement.get('needs_change') and i < len(links_to_check):
                        link = links_to_check[i]['element']
                        # Store original text as title for context
                        if not link.get('title'):
                            link['title'] = improvement.get('improved', improvement['original'])
                        # Add aria-label with improved text
                        link['aria-label'] = improvement.get('improved', improvement['original'])
                        self.report.add_fix(f"Added aria-label to improve link: '{improvement['original'][:30]}'")
            except Exception as e:
                self.report.add_warning(f"Could not improve link text: {str(e)}")

    def _add_skip_link(self, soup: BeautifulSoup):
        """Add skip navigation link."""
        body = soup.find('body')
        if not body or not isinstance(body, Tag):
            return

        # Check if skip link already exists
        existing_skip = soup.find('a', href='#main-content') or soup.find('a', href='#main') or soup.find('a', {'class': 'skip-link'})
        if existing_skip:
            return

        # Find or create main content area
        main = soup.find('main') or soup.find(id='main-content') or soup.find(id='main')

        if not main:
            # Look for content area
            content = soup.find(id='content') or soup.find(class_='content')
            if content and isinstance(content, Tag):
                content['id'] = 'main-content'
                main = content

        if main and isinstance(main, Tag):
            if not main.get('id'):
                main['id'] = 'main-content'

            # Create skip link
            skip_link = soup.new_tag('a', href=f"#{main.get('id')}")
            skip_link['class'] = 'skip-link'
            skip_link.string = 'Skip to main content'

            # Add CSS for skip link (visually hidden until focused)
            style = soup.find('style')
            skip_css = """
.skip-link {
    position: absolute;
    top: -40px;
    left: 0;
    background: #000;
    color: #fff;
    padding: 8px;
    z-index: 100;
}
.skip-link:focus {
    top: 0;
}
"""
            if style and isinstance(style, Tag):
                style.string = (style.string or '') + skip_css
            else:
                new_style = soup.new_tag('style')
                new_style.string = skip_css
                head = soup.find('head')
                if head and isinstance(head, Tag):
                    head.append(new_style)

            body.insert(0, skip_link)
            self.report.add_fix("Added skip navigation link")

    def _fix_form_labels(self, soup: BeautifulSoup):
        """Ensure form inputs have associated labels."""
        inputs = soup.find_all(['input', 'select', 'textarea'])

        for inp in inputs:
            if not isinstance(inp, Tag):
                continue

            input_type = inp.get('type', 'text')

            # Skip certain input types
            if input_type in ('hidden', 'submit', 'button', 'reset', 'image'):
                continue

            input_id = inp.get('id')

            # Check if there's an associated label
            has_label = False

            if input_id:
                label = soup.find('label', {'for': input_id})
                has_label = label is not None

            # Check if wrapped in label
            if not has_label:
                parent = inp.parent
                if parent and parent.name == 'label':
                    has_label = True

            # Check for aria-label or aria-labelledby
            if not has_label:
                has_label = inp.get('aria-label') or inp.get('aria-labelledby')

            if not has_label:
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="4.1.2",
                    severity=Severity.ERROR,
                    description="Form input missing label",
                    location=f"Input type='{input_type}' id='{input_id or 'none'}'"
                ))

                # Add aria-label based on placeholder or name
                placeholder = inp.get('placeholder')
                name = inp.get('name', '')

                if placeholder:
                    inp['aria-label'] = placeholder
                    self.report.add_fix(f"Added aria-label from placeholder: '{placeholder}'")
                elif name:
                    # Convert name to readable label
                    readable = name.replace('_', ' ').replace('-', ' ').title()
                    inp['aria-label'] = readable
                    self.report.add_fix(f"Added aria-label from name: '{readable}'")

    def _add_aria_landmarks(self, soup: BeautifulSoup):
        """Add ARIA landmarks to improve navigation."""
        # Check for main element
        main = soup.find('main')
        if not main:
            # Look for main content area
            content_divs = soup.find_all('div', id=re.compile(r'(main|content)', re.I))
            for div in content_divs:
                if isinstance(div, Tag) and not div.get('role'):
                    div['role'] = 'main'
                    self.report.add_fix("Added role='main' to content div")
                    break

        # Check for nav elements
        nav = soup.find('nav')
        if not nav:
            # Look for navigation areas
            nav_divs = soup.find_all('div', id=re.compile(r'nav', re.I))
            for div in nav_divs:
                if isinstance(div, Tag) and not div.get('role'):
                    div['role'] = 'navigation'
                    self.report.add_fix("Added role='navigation' to nav div")

        # Check for header/banner
        header = soup.find('header')
        if header and isinstance(header, Tag) and not header.get('role'):
            header['role'] = 'banner'

        # Check for footer/contentinfo
        footer = soup.find('footer')
        if footer and isinstance(footer, Tag) and not footer.get('role'):
            footer['role'] = 'contentinfo'

    def _fix_color_only_information(self, soup: BeautifulSoup):
        """Fix color-only information by adding additional visual indicators."""
        # Find elements with inline color styles
        colored_elements = soup.find_all(style=re.compile(r'color\s*:', re.IGNORECASE))

        for element in colored_elements:
            if not isinstance(element, Tag):
                continue

            text = element.get_text(strip=True)
            if not text:
                continue

            style = element.get('style', '')

            # Check if already has additional formatting
            has_bold = 'font-weight' in style.lower() or element.find('strong') or element.find('b')
            has_underline = 'text-decoration' in style.lower()

            if not has_bold and not has_underline:
                # Add font-weight: bold to existing style
                if style.strip() and not style.strip().endswith(';'):
                    style += ';'
                style += ' font-weight: bold;'
                element['style'] = style.strip()
                self.report.add_fix(f"Added bold to color-emphasized text: '{text[:30]}...'" if len(text) > 30 else f"Added bold to color-emphasized text: '{text}'")

        # Find elements with color-related classes
        color_classes = ['text-red', 'text-green', 'text-blue', 'text-danger', 'text-warning',
                         'text-success', 'text-error', 'red', 'green', 'blue', 'danger', 'warning', 'error', 'success']

        for color_class in color_classes:
            elements = soup.find_all(class_=re.compile(rf'\b{color_class}\b', re.IGNORECASE))
            for element in elements:
                if not isinstance(element, Tag):
                    continue

                text = element.get_text(strip=True)
                if not text:
                    continue

                # Check if already has bold/strong formatting
                has_bold = element.find('strong') or element.find('b')
                style = element.get('style', '')
                has_bold_style = 'font-weight' in style.lower()

                if not has_bold and not has_bold_style:
                    # Wrap content in strong tag
                    if element.string:
                        new_strong = soup.new_tag('strong')
                        new_strong.string = element.string
                        element.string = ''
                        element.append(new_strong)
                        self.report.add_fix(f"Added bold to class-colored text: '{text[:30]}...'" if len(text) > 30 else f"Added bold to class-colored text: '{text}'")

    def _add_math_descriptions(self, soup: BeautifulSoup):
        """Add descriptions for mathematical equations in HTML."""
        # Find MathML elements
        math_elements = soup.find_all('math')

        for math_el in math_elements:
            if not isinstance(math_el, Tag):
                continue

            # Check for existing alt text or aria-label
            has_description = math_el.get('alttext') or math_el.get('aria-label')

            if not has_description:
                # Get the math content
                math_content = math_el.get_text(strip=True)

                # Check for surrounding descriptive text
                parent = math_el.parent
                surrounding_text = parent.get_text(strip=True)[:200] if parent else ''

                has_nearby_description = any(keyword in surrounding_text.lower() for keyword in [
                    'where', 'equation', 'formula', 'defined as', 'given by',
                    'expressed as', 'calculated', 'represents', 'shows'
                ])

                if not has_nearby_description:
                    try:
                        description = self._generate_math_description(math_content)
                        if description:
                            math_el['aria-label'] = description
                            self.report.add_fix(f"Added aria-label for math element")
                    except Exception:
                        self.report.add_issue(AccessibilityIssue(
                            wcag_criterion="1.1.1",
                            severity=Severity.WARNING,
                            description="Mathematical notation lacks textual description",
                            suggestion="Add aria-label or alttext attribute to math element"
                        ))

        # Find LaTeX-style math (e.g., in script tags or spans with math classes)
        latex_containers = soup.find_all(class_=re.compile(r'math|latex|equation|katex|mathjax', re.IGNORECASE))

        for container in latex_containers:
            if not isinstance(container, Tag):
                continue

            # Check for aria-label
            if not container.get('aria-label'):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.1.1",
                    severity=Severity.INFO,
                    description="LaTeX/Math container may need aria-label",
                    suggestion="Add aria-label with plain language description"
                ))

    def _generate_math_description(self, math_content: str) -> str:
        """Generate description for math equation using Claude."""
        try:
            response = self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=100,
                system="Describe what this mathematical expression represents in plain language (one brief phrase). Return ONLY the description.",
                messages=[{
                    "role": "user",
                    "content": f"Math expression: {math_content}\n\nDescribe what this represents:"
                }]
            )
            return response.content[0].text.strip()
        except Exception:
            return None

    def _fix_ambiguous_references(self, soup: BeautifulSoup):
        """Fix ambiguous page/visual-only references in HTML."""
        # Get all text content
        text_elements = soup.find_all(string=True)

        for text_el in text_elements:
            text = str(text_el)

            # Pattern for "see page X" or "on page X"
            page_ref_pattern = r'([Ss]ee|[Oo]n|[Rr]efer to)\s+page\s+(\d+)'
            for match in re.finditer(page_ref_pattern, text):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.3.1",
                    severity=Severity.WARNING,
                    description=f"Ambiguous page reference: '{match.group(0)}'",
                    suggestion="Use descriptive links instead of page numbers"
                ))

            # Pattern for "above" or "below" references
            position_ref_pattern = r'(the|see|as shown)\s+(above|below|following|previous)\s+(figure|table|section|image|diagram)'
            for match in re.finditer(position_ref_pattern, text, re.IGNORECASE):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.3.1",
                    severity=Severity.INFO,
                    description=f"Position-based reference: '{match.group(0)}'",
                    suggestion="Consider using id/href links to referenced content"
                ))

            # Pattern for color-only references
            color_ref_pattern = r'(the|see|marked in|shown in|highlighted in)\s+(red|green|blue|yellow|orange|purple|pink)\s+(text|items?|sections?|areas?)?'
            for match in re.finditer(color_ref_pattern, text, re.IGNORECASE):
                self.report.add_issue(AccessibilityIssue(
                    wcag_criterion="1.4.1",
                    severity=Severity.ERROR,
                    description=f"Color-only reference: '{match.group(0)}'",
                    suggestion="Add non-color indicator in addition to color"
                ))

    def _check_document_structure(self, soup: BeautifulSoup):
        """Check for proper document structure in HTML."""
        # Check for elements using style instead of semantic markup
        bold_styled = soup.find_all(style=re.compile(r'font-weight\s*:\s*(bold|[6-9]00)', re.IGNORECASE))
        large_styled = soup.find_all(style=re.compile(r'font-size\s*:\s*(large|x-large|\d{2,}px)', re.IGNORECASE))

        potential_heading_issues = 0
        for element in bold_styled + large_styled:
            if isinstance(element, Tag):
                # Check if this styled element is not a heading but looks like one
                if element.name not in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'th', 'strong', 'b']:
                    text = element.get_text(strip=True)
                    # Short text that's styled could be a heading
                    if text and len(text) < 100:
                        potential_heading_issues += 1

        if potential_heading_issues > 0:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.WARNING,
                description=f"Document may use styling instead of proper heading elements ({potential_heading_issues} potential instances)",
                suggestion="Use <h1>-<h6> elements instead of styled text for headings"
            ))

        # Check for presence of h1
        h1 = soup.find('h1')
        if not h1:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="2.4.2",
                severity=Severity.WARNING,
                description="Document missing <h1> element",
                suggestion="Add an <h1> element for the main page heading"
            ))

        # Check for semantic structure elements
        has_main = soup.find('main') is not None
        has_nav = soup.find('nav') is not None
        has_header = soup.find('header') is not None
        has_footer = soup.find('footer') is not None

        if not has_main:
            self.report.add_issue(AccessibilityIssue(
                wcag_criterion="1.3.1",
                severity=Severity.INFO,
                description="Document missing <main> element",
                suggestion="Wrap main content in <main> for better screen reader navigation"
            ))
