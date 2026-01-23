#!/usr/bin/env python3
"""
Test script for PDF conversion to WCAG 2.1 AA compliance.
Tests with Lecture34.pdf
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from processors.pdf_processor import PDFProcessor
from utils.claude_client import ClaudeClient


def test_pdf_conversion(pdf_path: str, output_path: str = None):
    """
    Test PDF conversion to WCAG 2.1 compliant format.

    Args:
        pdf_path: Path to input PDF
        output_path: Path for output PDF (optional)
    """
    print(f"Testing PDF conversion with: {pdf_path}")
    print("=" * 80)

    # Check if file exists
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        print(f"❌ Error: File not found: {pdf_path}")
        return False

    # Initialize Claude client
    try:
        print("\n📡 Initializing Claude API client...")
        claude_client = ClaudeClient()
        print("✅ Claude client initialized")
    except Exception as e:
        print(f"❌ Error initializing Claude client: {e}")
        print("Make sure CLAUDE_API_KEY is set in your .env file")
        return False

    # Read PDF content
    print(f"\n📄 Reading PDF: {pdf_file.name}")
    with open(pdf_file, 'rb') as f:
        pdf_content = f.read()
    print(f"✅ Read {len(pdf_content):,} bytes")

    # Process PDF
    print("\n🔄 Processing PDF for WCAG 2.1 AA compliance...")
    print("This may take a few moments as images are analyzed...")

    try:
        processor = PDFProcessor(claude_client)
        accessible_pdf = processor.process(pdf_content, pdf_file.name)
        report = processor.get_report()

        print("\n✅ Processing complete!")

        # Display report
        print("\n" + "=" * 80)
        print("📊 ACCESSIBILITY REPORT")
        print("=" * 80)

        print(f"\n{report.get_summary()}")

        # Fixes applied
        if report.fixes_applied:
            print(f"\n✅ Fixes Applied ({len(report.fixes_applied)}):")
            for i, fix in enumerate(report.fixes_applied, 1):
                print(f"   {i}. {fix}")

        # Issues found
        errors = [i for i in report.issues if i.severity.value == "error"]
        warnings = [i for i in report.issues if i.severity.value == "warning"]
        info = [i for i in report.issues if i.severity.value == "info"]

        if errors:
            print(f"\n❌ Errors ({len(errors)}) - Require Manual Attention:")
            for i, issue in enumerate(errors, 1):
                print(f"   {i}. [{issue.wcag_criterion}] {issue.description}")
                if issue.location:
                    print(f"      Location: {issue.location}")
                if issue.suggestion:
                    print(f"      Suggestion: {issue.suggestion}")

        if warnings:
            print(f"\n⚠️  Warnings ({len(warnings)}):")
            for i, issue in enumerate(warnings, 1):
                print(f"   {i}. [{issue.wcag_criterion}] {issue.description}")
                if issue.suggestion:
                    print(f"      Suggestion: {issue.suggestion}")

        if info:
            print(f"\nℹ️  Info ({len(info)}):")
            for i, issue in enumerate(info, 1):
                print(f"   {i}. [{issue.wcag_criterion}] {issue.description}")

        # General notes
        if report.warnings:
            print(f"\n📝 Notes:")
            for warning in report.warnings:
                print(f"   • {warning}")

        # Save output
        if output_path:
            output_file = Path(output_path)
        else:
            output_file = pdf_file.parent / f"accessible_{pdf_file.name}"

        print(f"\n💾 Saving accessible PDF to: {output_file}")
        with open(output_file, 'wb') as f:
            f.write(accessible_pdf)

        print(f"✅ Saved {len(accessible_pdf):,} bytes")

        print("\n" + "=" * 80)
        print("✅ TEST COMPLETE")
        print("=" * 80)

        # Summary
        print("\n📋 Summary:")
        print(f"   • Input file: {pdf_file}")
        print(f"   • Output file: {output_file}")
        print(f"   • Fixes applied: {len(report.fixes_applied)}")
        print(f"   • Errors: {len(errors)}")
        print(f"   • Warnings: {len(warnings)}")
        print(f"   • Info: {len(info)}")

        return True

    except Exception as e:
        print(f"\n❌ Error during processing: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Default to Lecture34.pdf in current directory
    input_pdf = "Lecture34.pdf"
    output_pdf = None

    # Allow command line arguments
    if len(sys.argv) > 1:
        input_pdf = sys.argv[1]
    if len(sys.argv) > 2:
        output_pdf = sys.argv[2]

    success = test_pdf_conversion(input_pdf, output_pdf)
    sys.exit(0 if success else 1)
