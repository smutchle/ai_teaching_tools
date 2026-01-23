#!/usr/bin/env python3
"""
Verify the improvements in the accessible PDF.
"""

import fitz  # PyMuPDF

def verify_pdf(pdf_path):
    """Verify accessibility improvements in PDF."""
    print(f"Verifying: {pdf_path}")
    print("=" * 80)

    doc = fitz.open(pdf_path)

    # Check metadata
    print("\n📋 METADATA:")
    print("-" * 80)
    metadata = doc.metadata
    for key in ['title', 'author', 'subject', 'producer', 'creator']:
        value = metadata.get(key, 'Not set')
        print(f"  {key.capitalize():<15} {value}")

    # Check language
    print("\n🌐 LANGUAGE SETTING:")
    print("-" * 80)
    try:
        catalog = doc.pdf_catalog()
        lang = doc.xref_get_key(catalog, "Lang")
        print(f"  PDF Catalog /Lang: {lang}")
    except Exception as e:
        print(f"  Could not read language: {e}")

    # Check bookmarks
    print("\n📚 BOOKMARKS/OUTLINE:")
    print("-" * 80)
    toc = doc.get_toc()
    if toc:
        print(f"  Total bookmarks: {len(toc)}")
        print(f"\n  First 10 bookmarks:")
        for i, (level, title, page) in enumerate(toc[:10], 1):
            indent = "  " * level
            print(f"    {i}. {indent}[Level {level}] {title} (Page {page})")
        if len(toc) > 10:
            print(f"    ... and {len(toc) - 10} more")
    else:
        print("  No bookmarks found")

    # Check images and alt text
    print("\n🖼️  IMAGES WITH ALT TEXT:")
    print("-" * 80)
    total_images = 0
    images_with_alt = 0

    for page_num in range(min(len(doc), 5)):  # Check first 5 pages
        page = doc[page_num]
        image_list = page.get_images(full=True)

        for img_info in image_list:
            xref = img_info[0]
            total_images += 1

            try:
                # Try to get alt text
                alt_text = doc.xref_get_key(xref, "Alt")
                if alt_text and alt_text[0] != 'null':
                    images_with_alt += 1
                    # Clean up the alt text display
                    alt_display = alt_text[1] if isinstance(alt_text, tuple) else str(alt_text)
                    if len(alt_display) > 60:
                        alt_display = alt_display[:60] + "..."
                    print(f"  Page {page_num + 1}, Image {total_images}: ✅ {alt_display}")
            except Exception as e:
                pass

    print(f"\n  Summary: {images_with_alt} out of {total_images} images have embedded alt text")

    # Check page count
    print("\n📄 DOCUMENT STRUCTURE:")
    print("-" * 80)
    print(f"  Total pages: {len(doc)}")
    print(f"  Has text content: {'Yes' if doc[0].get_text().strip() else 'No'}")

    # File size
    import os
    file_size = os.path.getsize(pdf_path)
    print(f"  File size: {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")

    doc.close()

    print("\n" + "=" * 80)
    print("✅ Verification complete!")
    print("=" * 80)


if __name__ == "__main__":
    import sys

    pdf_file = "accessible_Lecture34_final.pdf"
    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]

    verify_pdf(pdf_file)
