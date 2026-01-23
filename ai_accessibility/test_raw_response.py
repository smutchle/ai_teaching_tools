#!/usr/bin/env python3
"""
Test script to see raw Claude API response for image description.
"""

import sys
import os
import base64
from pathlib import Path
import fitz  # PyMuPDF
from anthropic import Anthropic
from dotenv import load_dotenv

# Load environment
load_dotenv()


def test_raw_response():
    """Test raw response from Claude API."""
    print("Testing raw Claude API response for image description...")
    print("=" * 80)

    # Initialize client
    api_key = os.getenv("CLAUDE_API_KEY")
    if not api_key:
        print("❌ CLAUDE_API_KEY not found")
        return False

    client = Anthropic(api_key=api_key)
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

    print(f"✅ Using model: {model}")

    # Open PDF and get first image
    pdf_path = "Lecture34.pdf"
    doc = fitz.open(pdf_path)

    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        if image_list:
            xref = image_list[0][0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            print(f"\n📸 Image info:")
            print(f"   Format: {image_ext}")
            print(f"   Size: {len(image_bytes):,} bytes")

            # Prepare request
            media_type_map = {
                'png': 'image/png',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'gif': 'image/gif',
                'bmp': 'image/bmp',
            }
            media_type = media_type_map.get(image_ext.lower(), 'image/png')
            encoded_image = base64.standard_b64encode(image_bytes).decode("utf-8")

            page_text = page.get_text()[:500]
            context = f"PDF page {page_num + 1}. Surrounding text: {page_text}"

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

            print("\n🤖 Calling Claude API...")
            try:
                response = client.messages.create(
                    model=model,
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

                print("\n✅ Got response!")
                print(f"\n📝 Raw response text:")
                print("-" * 80)
                raw_text = response.content[0].text
                print(raw_text)
                print("-" * 80)

                # Try to parse as JSON
                import json
                try:
                    parsed = json.loads(raw_text)
                    print("\n✅ Successfully parsed as JSON:")
                    print(f"   Alt text: {parsed.get('alt_text')}")
                    print(f"   Is complex: {parsed.get('is_complex')}")
                    if parsed.get('long_description'):
                        print(f"   Long description: {parsed.get('long_description')[:200]}...")
                except json.JSONDecodeError as e:
                    print(f"\n❌ JSON parsing failed: {e}")
                    print("\nThis is why 'Image description unavailable' is returned!")

                return True

            except Exception as e:
                print(f"\n❌ Error calling API: {e}")
                import traceback
                traceback.print_exc()
                return False

    print("\n⚠️  No images found")
    return False


if __name__ == "__main__":
    success = test_raw_response()
    sys.exit(0 if success else 1)
