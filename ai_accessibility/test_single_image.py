#!/usr/bin/env python3
"""
Test script to debug image alt text generation.
"""

import sys
from pathlib import Path
import fitz  # PyMuPDF

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from utils.claude_client import ClaudeClient


def test_single_image_description():
    """Test alt text generation for a single image from the PDF."""
    print("Testing image description generation...")
    print("=" * 80)

    # Initialize Claude client
    try:
        print("\n📡 Initializing Claude API client...")
        claude_client = ClaudeClient()
        print("✅ Claude client initialized")
        print(f"   Model: {claude_client.model}")
    except Exception as e:
        print(f"❌ Error initializing Claude client: {e}")
        return False

    # Open PDF
    pdf_path = "Lecture34.pdf"
    print(f"\n📄 Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)

    # Get first image
    print("\n🔍 Extracting first image from PDF...")
    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        if image_list:
            print(f"✅ Found {len(image_list)} image(s) on page {page_num + 1}")

            # Get first image
            xref = image_list[0][0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            print(f"   Image format: {image_ext}")
            print(f"   Image size: {len(image_bytes):,} bytes")

            # Determine media type
            media_type_map = {
                'png': 'image/png',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'gif': 'image/gif',
                'bmp': 'image/bmp',
            }
            media_type = media_type_map.get(image_ext.lower(), 'image/png')
            print(f"   Media type: {media_type}")

            # Get page context
            page_text = page.get_text()[:500]
            context = f"PDF page {page_num + 1}. Surrounding text: {page_text}"

            print("\n🤖 Calling Claude API to describe image...")
            try:
                result = claude_client.describe_complex_image(
                    image_data=image_bytes,
                    media_type=media_type,
                    context=context
                )

                print("\n✅ Successfully got image description!")
                print("\nResult:")
                print(f"   Alt text: {result.get('alt_text')}")
                print(f"   Is complex: {result.get('is_complex')}")
                if result.get('long_description'):
                    print(f"   Long description: {result.get('long_description')[:200]}...")

                return True

            except Exception as e:
                print(f"\n❌ Error generating alt text: {e}")
                import traceback
                traceback.print_exc()
                return False

    print("\n⚠️  No images found in PDF")
    return False


if __name__ == "__main__":
    success = test_single_image_description()
    sys.exit(0 if success else 1)
