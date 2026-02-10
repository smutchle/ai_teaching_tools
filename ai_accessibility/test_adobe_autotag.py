#!/usr/bin/env python3
"""
Test script for Adobe Auto-Tag PDF processor.

Usage:
    python test_adobe_autotag.py <input.pdf> [output.pdf]

Requirements:
    - Adobe PDF Services API credentials in .env file
    - pdfservices-sdk installed (pip install pdfservices-sdk)
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from processors.pdf_adobe_autotag_processor import AdobeAutoTagPDFProcessor
from utils.claude_client import ClaudeClient


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_adobe_autotag.py <input.pdf> [output.pdf]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path.parent / f"{input_path.stem}_adobe_tagged.pdf"

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    print(f"Testing Adobe Auto-Tag PDF processor...")
    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print()

    try:
        # Create processor
        print("Initializing Adobe Auto-Tag processor...")
        claude_client = ClaudeClient()
        processor = AdobeAutoTagPDFProcessor(claude_client)

        # Read input PDF
        print(f"Reading input PDF: {input_path}")
        with open(input_path, 'rb') as f:
            content = f.read()

        # Process PDF
        print("Processing PDF with Adobe Auto-Tag API...")
        print("This may take 30-60 seconds depending on PDF size...")
        processed_content = processor.process(content, input_path.name)

        # Save output
        print(f"Saving tagged PDF: {output_path}")
        with open(output_path, 'wb') as f:
            f.write(processed_content)

        # Display report
        report = processor.get_report()
        print("\n" + "="*80)
        print("ACCESSIBILITY REPORT")
        print("="*80)
        print(f"\n{report.get_summary()}\n")

        if report.fixes_applied:
            print("FIXES APPLIED:")
            for fix in report.fixes_applied:
                print(f"  ✓ {fix}")

        if report.warnings:
            print("\nWARNINGS:")
            for warning in report.warnings:
                print(f"  ⚠ {warning}")

        if report.issues:
            print("\nISSUES FOUND:")
            for issue in report.issues:
                severity_emoji = {"ERROR": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}.get(issue.severity.name, "•")
                print(f"  {severity_emoji} [{issue.wcag_criterion}] {issue.description}")
                if issue.suggestion:
                    print(f"     → {issue.suggestion}")

        print("\n" + "="*80)
        print(f"✅ SUCCESS! Tagged PDF saved to: {output_path}")
        print("="*80)

        # Check for accessibility report
        report_dir = Path(__file__).parent / "output" / "adobe_reports"
        if report_dir.exists():
            reports = sorted(report_dir.glob(f"{input_path.stem}*.xlsx"), key=lambda x: x.stat().st_mtime, reverse=True)
            if reports:
                print(f"\n📊 Adobe accessibility report (XLSX): {reports[0]}")

    except EnvironmentError as e:
        print(f"\n❌ ERROR: {e}")
        print("\nTo use Adobe Auto-Tag, you need to:")
        print("1. Get Adobe PDF Services credentials at:")
        print("   https://developer.adobe.com/document-services/docs/overview/pdf-services-api/")
        print("2. Add to your .env file:")
        print("   PDF_SERVICES_CLIENT_ID=your_client_id")
        print("   PDF_SERVICES_CLIENT_SECRET=your_client_secret")
        sys.exit(1)

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
