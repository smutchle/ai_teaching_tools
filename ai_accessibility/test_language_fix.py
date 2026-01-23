#!/usr/bin/env python3
"""
Test different approaches to setting PDF language for PAC detection.
"""

import fitz
import sys


def test_language_setting(input_pdf, output_pdf):
    """Test various methods of setting PDF language."""
    print(f"Testing language setting on: {input_pdf}")
    print("=" * 80)

    doc = fitz.open(input_pdf)

    try:
        catalog_xref = doc.pdf_catalog()
        print(f"\nCatalog xref: {catalog_xref}")

        # Check current language
        print("\nChecking current language...")
        try:
            lang = doc.xref_get_key(catalog_xref, "Lang")
            print(f"  Current /Lang: {lang}")
        except Exception as e:
            print(f"  Error reading /Lang: {e}")

        # Method 1: Try setting as PDF string with parentheses
        print("\nMethod 1: Setting as (en-US)...")
        try:
            doc.xref_set_key(catalog_xref, "Lang", "(en-US)")
            lang = doc.xref_get_key(catalog_xref, "Lang")
            print(f"  Result: {lang}")
        except Exception as e:
            print(f"  Error: {e}")

        # Method 2: Get the PDF catalog object and update it
        print("\nMethod 2: Getting catalog object...")
        try:
            catalog_obj = doc.xref_object(catalog_xref)
            print(f"  Catalog object preview: {catalog_obj[:200]}...")

            # Check if /Lang is in there
            if "/Lang" in catalog_obj:
                print("  /Lang found in catalog")
            else:
                print("  /Lang NOT found in catalog")

        except Exception as e:
            print(f"  Error: {e}")

        # Method 3: Try updating the entire catalog object
        print("\nMethod 3: Updating catalog stream...")
        try:
            # Read current catalog
            catalog_str = doc.xref_object(catalog_xref, compressed=False)

            # Check if /Lang exists and update or add it
            if b"/Lang" in catalog_str:
                # Replace existing
                import re
                catalog_str = re.sub(
                    rb'/Lang\s+[^\s/>]+',
                    b'/Lang (en-US)',
                    catalog_str
                )
                print("  Updated existing /Lang")
            else:
                # Add new /Lang entry before closing >>
                catalog_str = catalog_str.replace(b'>>', b'/Lang (en-US)\n>>')
                print("  Added new /Lang entry")

            # Update the catalog
            doc.xref_stream(catalog_xref, catalog_str)

            # Verify
            lang = doc.xref_get_key(catalog_xref, "Lang")
            print(f"  Verification: {lang}")

        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()

        # Save and verify
        print(f"\nSaving to: {output_pdf}")
        doc.save(output_pdf, garbage=4, deflate=True)

        # Re-open and check
        print("\nRe-opening saved PDF to verify...")
        doc.close()
        doc = fitz.open(output_pdf)

        catalog_xref = doc.pdf_catalog()
        lang = doc.xref_get_key(catalog_xref, "Lang")
        print(f"  Final /Lang value: {lang}")

        if lang and lang[0] != 'null':
            print("\n✅ SUCCESS: Language is set!")
        else:
            print("\n❌ FAILED: Language is still null")

    finally:
        doc.close()


if __name__ == "__main__":
    input_file = "Lecture34.pdf"
    output_file = "test_language_output.pdf"

    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]

    test_language_setting(input_file, output_file)
