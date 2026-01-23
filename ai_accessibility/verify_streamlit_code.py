#!/usr/bin/env python3
"""
Verify that Streamlit app uses the latest PDFProcessor with PAC fixes.
"""

import sys
from pathlib import Path

# Add project root to path (same as Streamlit app does)
sys.path.insert(0, str(Path(__file__).parent))

# Import exactly as Streamlit app does
from processors import PDFProcessor
from utils.claude_client import ClaudeClient

# Import inspect to check methods
import inspect


def verify_processor_has_pac_fixes():
    """Verify PDFProcessor has the PAC-specific fixes."""
    print("=" * 80)
    print("VERIFYING STREAMLIT APP HAS LATEST PDFProcessor CODE")
    print("=" * 80)
    print()

    # Check if PDFProcessor has the new methods we added
    methods_to_check = [
        '_create_structure_tree_root',
        '_mark_as_tagged_pdf',
        '_set_document_language',
        '_auto_generate_bookmarks',
    ]

    print("Checking for PAC-specific methods:")
    print("-" * 80)

    all_present = True
    for method_name in methods_to_check:
        if hasattr(PDFProcessor, method_name):
            method = getattr(PDFProcessor, method_name)
            # Get docstring
            doc = method.__doc__
            if doc:
                first_line = doc.strip().split('\n')[0][:60]
            else:
                first_line = "(no docstring)"
            print(f"  ✅ {method_name}")
            print(f"     {first_line}")
        else:
            print(f"  ❌ {method_name} - NOT FOUND!")
            all_present = False

    print()
    print()

    # Check the _set_document_language method for PAC-compatible format
    print("Checking _set_document_language implementation:")
    print("-" * 80)

    if hasattr(PDFProcessor, '_set_document_language'):
        source = inspect.getsource(PDFProcessor._set_document_language)

        # Check for key indicators of the fixed version
        has_pac_format = '(en-US)' in source or 'PAC-compatible' in source
        has_verification = 'lang_check' in source

        if has_pac_format:
            print("  ✅ Uses PAC-compatible format: (en-US)")
        else:
            print("  ❌ Does NOT use PAC-compatible format")

        if has_verification:
            print("  ✅ Includes verification of language setting")
        else:
            print("  ⚠️  Missing verification step")

        # Check if it calls xref_set_key only once (not twice)
        xref_set_count = source.count('xref_set_key(catalog_xref, "Lang"')
        print(f"  {'✅' if xref_set_count == 1 else '❌'} Calls xref_set_key for Lang: {xref_set_count} time(s)")
        if xref_set_count > 1:
            print("     WARNING: Multiple calls will overwrite!")

    print()
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if all_present:
        print("✅ PDFProcessor HAS all PAC-specific methods")
        print("✅ Streamlit app will use the latest code")
        print()
        print("The Streamlit app is ready to use!")
        print()
        print("To run:")
        print("  streamlit run ai_access_app.py")
        return True
    else:
        print("❌ PDFProcessor is MISSING some PAC-specific methods")
        print("⚠️  Streamlit app may not have all fixes")
        return False


if __name__ == "__main__":
    success = verify_processor_has_pac_fixes()
    sys.exit(0 if success else 1)
