#!/usr/bin/env python3
"""
Test script for PDF→QMD→PDF conversion using Quarto.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from processors.pdf_to_qmd_processor import PDFToQMDProcessor
from utils.claude_client import ClaudeClient


def test_pdf_to_qmd_to_pdf(pdf_path: str, output_pdf_path: str = None, save_qmd: bool = True):
    """
    Test PDF→QMD→PDF conversion using Quarto.

    Args:
        pdf_path: Path to input PDF
        output_pdf_path: Path for output PDF (optional)
        save_qmd: Whether to save intermediate QMD file
    """
    print(f"Testing PDF→QMD→PDF conversion with: {pdf_path}")
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

    # Process PDF → QMD → PDF
    print("\n🔄 Converting PDF → QMD → PDF using Quarto...")
    print("This may take a few moments:")
    print("  1. Extracting content from PDF")
    print("  2. Generating AI alt text for images")
    print("  3. Creating Quarto Markdown")
    print("  4. Rendering QMD to accessible PDF with Quarto")

    try:
        processor = PDFToQMDProcessor(claude_client, render_to_pdf=True)
        pdf_output = processor.process(pdf_content, pdf_file.name)
        qmd_content = processor.get_qmd_content()
        report = processor.get_report()

        print("\n✅ Conversion complete!")

        # Display report
        print("\n" + "=" * 80)
        print("📊 CONVERSION REPORT")
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
            print(f"\n❌ Errors ({len(errors)}):")
            for i, issue in enumerate(errors, 1):
                print(f"   {i}. [{issue.wcag_criterion}] {issue.description}")
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

        # Save output PDF
        if output_pdf_path:
            output_file = Path(output_pdf_path)
        else:
            output_file = pdf_file.parent / f"accessible_{pdf_file.name}"

        print(f"\n💾 Saving accessible PDF to: {output_file}")
        with open(output_file, 'wb') as f:
            f.write(pdf_output)

        print(f"✅ Saved {len(pdf_output):,} bytes")

        # Optionally save QMD
        if save_qmd and qmd_content:
            qmd_file = pdf_file.parent / f"{pdf_file.stem}.qmd"
            print(f"\n💾 Saving intermediate QMD to: {qmd_file}")
            with open(qmd_file, 'w', encoding='utf-8') as f:
                f.write(qmd_content)
            print(f"✅ Saved intermediate QMD file")

            # Show sample of QMD content
            print("\n" + "=" * 80)
            print("📄 QMD PREVIEW (first 500 characters)")
            print("=" * 80)
            sample = qmd_content[:500]
            print(sample)
            if len(qmd_content) > 500:
                print("\n... (truncated)")

        print("\n" + "=" * 80)
        print("✅ CONVERSION COMPLETE")
        print("=" * 80)

        # Summary
        print("\n📋 Summary:")
        print(f"   • Input file: {pdf_file}")
        print(f"   • Output file: {output_file}")
        print(f"   • Fixes applied: {len(report.fixes_applied)}")
        print(f"   • Errors: {len(errors)}")
        print(f"   • Warnings: {len(warnings)}")
        print(f"   • Info: {len(info)}")

        print("\n📖 Next Steps:")
        print(f"   1. Open the accessible PDF: {output_file}")
        print(f"   2. Validate with PAC (PDF Accessibility Checker)")
        if save_qmd and qmd_content:
            print(f"   3. Edit QMD if needed: {qmd_file}")
            print(f"   4. Re-render: quarto render {qmd_file} --to pdf")

        return True

    except Exception as e:
        print(f"\n❌ Error during conversion: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # Allow command line arguments
    if len(sys.argv) < 2:
        print("Usage: python test_pdf_to_qmd.py <input.pdf> [output.pdf]")
        print("\nExample:")
        print("  python test_pdf_to_qmd.py lecture.pdf")
        print("  python test_pdf_to_qmd.py lecture.pdf accessible_lecture.pdf")
        print("\nNote: Requires Quarto to be installed (https://quarto.org)")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_pdf = sys.argv[2] if len(sys.argv) > 2 else None

    success = test_pdf_to_qmd_to_pdf(input_pdf, output_pdf)
    sys.exit(0 if success else 1)
