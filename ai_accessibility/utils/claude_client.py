"""Claude API client for accessibility-focused operations."""

import os
import base64
from typing import Optional
from dotenv import load_dotenv
from anthropic import Anthropic


class ClaudeClient:
    """Wrapper for Claude API with accessibility-focused prompts."""

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("CLAUDE_API_KEY")
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

        if not self.api_key:
            raise ValueError("CLAUDE_API_KEY not found in environment")

        self.client = Anthropic(api_key=self.api_key)

    def generate_alt_text(
        self,
        image_data: Optional[bytes] = None,
        image_context: str = "",
        surrounding_text: str = "",
        media_type: str = "image/png"
    ) -> str:
        """
        Generate WCAG-compliant alt text for an image.

        Args:
            image_data: Raw image bytes (if available)
            image_context: Description of where/how the image is used
            surrounding_text: Text around the image for context
            media_type: MIME type of the image

        Returns:
            Descriptive alt text following WCAG guidelines
        """
        system_prompt = """You are an accessibility expert generating alt text for images.
Follow WCAG 2.1 AA guidelines:
- Be concise but descriptive (typically 125 characters or less)
- Describe the content and function of the image
- Don't start with "Image of" or "Picture of"
- If the image contains text, include that text
- If the image is decorative, respond with exactly: DECORATIVE
- Consider the context in which the image appears
- Focus on what's important for understanding the content

Respond with ONLY the alt text, nothing else."""

        messages = []

        if image_data:
            # Include the actual image for analysis
            encoded_image = base64.standard_b64encode(image_data).decode("utf-8")
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": encoded_image,
                    },
                },
                {
                    "type": "text",
                    "text": f"Generate alt text for this image.\n\nContext: {image_context}\n\nSurrounding text: {surrounding_text}"
                }
            ]
        else:
            # No image data, use context only
            content = f"Generate alt text based on this context (no image available):\n\nImage context: {image_context}\n\nSurrounding text: {surrounding_text}"

        messages.append({"role": "user", "content": content})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            system=system_prompt,
            messages=messages
        )

        return response.content[0].text.strip()

    def analyze_heading_structure(self, content: str, format_type: str) -> dict:
        """
        Analyze and suggest fixes for heading hierarchy.

        Args:
            content: Document content
            format_type: Type of document (html, markdown, latex)

        Returns:
            Dictionary with analysis and suggested fixes
        """
        system_prompt = """You are an accessibility expert analyzing document heading structure.
Check for WCAG 2.1 AA compliance:
- Headings should not skip levels (e.g., h1 to h3 without h2)
- There should typically be one h1 per page/document
- Headings should be descriptive and meaningful
- Headings should create a logical outline of the content

Respond in JSON format:
{
    "issues": [{"location": "...", "current": "...", "suggested": "...", "reason": "..."}],
    "is_valid": true/false,
    "summary": "brief summary of findings"
}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Analyze the heading structure in this {format_type} document:\n\n{content[:8000]}"
            }]
        )

        import json
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return {"issues": [], "is_valid": True, "summary": "Could not parse response"}

    def improve_link_text(self, links: list[dict]) -> list[dict]:
        """
        Improve link text to be more descriptive.

        Args:
            links: List of dicts with 'text' and 'href' keys

        Returns:
            List of dicts with original and improved text
        """
        if not links:
            return []

        system_prompt = """You are an accessibility expert improving link text for WCAG 2.1 AA compliance.

CRITICAL: Generic link text like "here", "click here", "read more", "learn more", "link", "this link", "more", "info", "details" MUST ALWAYS be changed. These are accessibility violations.

Rules:
- Link text MUST describe the destination or purpose
- Generic text like "here", "click here", "read more" MUST be replaced
- Link text must make sense out of context
- Derive descriptive text from the URL domain/path when possible
- Keep improvements concise but descriptive (2-5 words typically)

Examples:
- "here" linking to climate.nasa.gov -> "NASA climate resources"
- "click here" linking to docs.pdf -> "documentation PDF"
- "read more" linking to /blog/article -> "full article"

IMPORTANT: Set needs_change to TRUE for ANY generic link text.

Respond in JSON format as a list:
[{"original": "...", "href": "...", "improved": "...", "needs_change": true/false}]"""

        links_text = "\n".join([f"- Text: '{l['text']}' -> URL: {l['href']}" for l in links[:20]])

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Improve these link texts if needed:\n\n{links_text}"
            }]
        )

        import json
        import re
        try:
            response_text = response.content[0].text
            # Strip markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response_text)
            if json_match:
                response_text = json_match.group(1)
            return json.loads(response_text)
        except json.JSONDecodeError:
            return [{"original": l["text"], "improved": l["text"], "needs_change": False} for l in links]

    def generate_table_caption(self, table_content: str, context: str = "") -> str:
        """
        Generate a descriptive caption for a table.

        Args:
            table_content: The table content (text representation)
            context: Surrounding text for context

        Returns:
            A descriptive caption for the table
        """
        system_prompt = """You are an accessibility expert generating table captions.
Create a brief, descriptive caption that:
- Summarizes what data the table contains
- Helps users decide if they need to read the table
- Is concise (one sentence)

Respond with ONLY the caption text, nothing else."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=100,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Generate a caption for this table:\n\n{table_content[:2000]}\n\nContext: {context}"
            }]
        )

        return response.content[0].text.strip()

    def analyze_document_accessibility(self, content: str, format_type: str) -> dict:
        """
        Perform comprehensive accessibility analysis on a document.

        Args:
            content: Document content
            format_type: Type of document

        Returns:
            Dictionary with accessibility issues and suggestions
        """
        system_prompt = """You are a WCAG 2.1 AA accessibility expert analyzing a document.
Identify accessibility issues and suggest fixes. Focus on:
- Missing alt text for images
- Heading hierarchy problems
- Non-descriptive link text
- Missing language declarations
- Table accessibility (headers, captions)
- Form label associations
- Color contrast concerns (if detectable)

Respond in JSON format:
{
    "issues": [
        {
            "wcag_criterion": "1.1.1",
            "severity": "error|warning",
            "description": "...",
            "location": "...",
            "suggestion": "..."
        }
    ],
    "score": 0-100,
    "summary": "..."
}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Analyze this {format_type} document for WCAG 2.1 AA accessibility:\n\n{content[:10000]}"
            }]
        )

        import json
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return {"issues": [], "score": 0, "summary": "Could not parse response"}

    def describe_complex_image(
        self,
        image_data: bytes,
        media_type: str = "image/png",
        context: str = ""
    ) -> dict:
        """
        Generate both alt text and long description for complex images.

        Args:
            image_data: Raw image bytes
            media_type: MIME type
            context: Context about where image appears

        Returns:
            Dict with 'alt_text' and 'long_description'
        """
        system_prompt = """You are an accessibility expert describing complex images.
For complex images (charts, diagrams, infographics), provide:
1. A brief alt text (125 chars or less)
2. A detailed long description that fully conveys the information

Respond in JSON format:
{
    "alt_text": "brief description",
    "long_description": "detailed description of all information conveyed",
    "is_complex": true/false
}"""

        encoded_image = base64.standard_b64encode(image_data).decode("utf-8")

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Describe this image for accessibility.\n\nContext: {context}"
                    }
                ]
            }]
        )

        import json
        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return {
                "alt_text": "Image description unavailable",
                "long_description": "",
                "is_complex": False
            }
