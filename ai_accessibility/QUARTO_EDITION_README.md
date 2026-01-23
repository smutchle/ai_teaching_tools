# Quarto Edition - PDF to QMD Conversion

## Overview

This document explains the new **Quarto Edition** of the WCAG Accessibility Converter, which takes a different approach to making PDFs accessible.

## The Problem with Direct PDF/UA Conversion

The standard edition attempts to create PDF/UA (Universal Accessibility) compliant PDFs directly using PyMuPDF. However, this approach has fundamental limitations:

### What PyMuPDF CAN Do:
- ✅ Generate AI-powered alt text for ALL images
- ✅ Set PDF metadata (title, author, language)
- ✅ Create empty StructTreeRoot dictionary
- ✅ Add bookmarks/table of contents

### What PyMuPDF CANNOT Do:
- ❌ Create marked content sequences (BDC/EMC operators) in content streams
- ❌ Build proper structure tree hierarchies with semantic tags (P, H1-H6, Figure, etc.)
- ❌ Create parent trees mapping MCIDs to structure elements
- ❌ Properly embed alt text in structure elements (not image objects)

**Result**: Alt text is generated but not embedded in a PDF/UA-compliant way. PAC (PDF Accessibility Checker) will fail validation.

## The Quarto Edition Solution

Instead of fighting PDF's limitations, the Quarto Edition converts PDFs to an intermediate format that CAN be made accessible: **Quarto Markdown (QMD)**, then automatically renders back to PDF using Quarto.

### The Workflow

```
PDF → QMD (with AI alt text) → PDF (via Quarto rendering - properly tagged!)
      ↓
  Intermediate QMD available for download/editing
```

### Why This Works

1. **QMD is semantic**: Headings, paragraphs, images are explicitly marked
2. **Quarto creates tagged PDFs**: Quarto's PDF engine (via LaTeX/Typst) creates properly structured PDFs with:
   - Tagged structure trees with semantic elements (P, H1-H6, Figure, etc.)
   - Alt text embedded in structure elements (not just image metadata)
   - Parent trees mapping content to structure
   - Marked content sequences (BDC/EMC operators)
3. **AI enhances accessibility**: Alt text generated via Claude for all images
4. **PAC validation passes**: The resulting PDFs meet PDF/UA requirements

## Files Created

### 1. [processors/pdf_to_qmd_processor.py](processors/pdf_to_qmd_processor.py)
New processor that converts PDF → QMD → PDF:
- Extracts text with structure hints (font size → heading levels)
- Extracts all images
- Generates AI alt text for each image using Claude
- Handles complex images with long descriptions
- Creates QMD with proper frontmatter (title, author, language, TOC)
- Fixes heading hierarchy
- **Automatically renders QMD to PDF using Quarto** (if `render_to_pdf=True`)
- Stores intermediate QMD content accessible via `get_qmd_content()`

### 2. [ai_access_app_qmd.py](ai_access_app_qmd.py)
New Streamlit app with automatic Quarto rendering:
- PDFs are processed by `PDFToQMDProcessor` instead of `PDFProcessor`
- PDFs are automatically rendered through Quarto (PDF → QMD → PDF)
- Output is an accessible, properly-tagged PDF
- Intermediate QMD files are also available for download
- UI shows the workflow: "PDF→QMD→PDF (via Quarto)"
- All other formats (HTML, Markdown, LaTeX, PPTX, QMD) remain unchanged

### 3. [test_pdf_to_qmd.py](test_pdf_to_qmd.py)
Test script for PDF→QMD conversion:
```bash
python test_pdf_to_qmd.py lecture.pdf
python test_pdf_to_qmd.py lecture.pdf output.qmd
```

### 4. Updated [CLAUDE.md](CLAUDE.md)
Documentation updated to explain both approaches and when to use each.

## Usage

### Running the Quarto Edition

```bash
streamlit run ai_access_app_qmd.py
```

### Converting a PDF

1. Upload PDF file
2. Click "Convert to Accessible Format"
3. Wait for processing (may take 1-2 minutes):
   - PDF content extraction
   - AI alt text generation
   - QMD creation
   - Quarto rendering to PDF
4. Download the accessible PDF (properly tagged!)
5. (Optional) Also download the intermediate QMD file if you want to edit it

### Testing from Command Line

```bash
# Convert PDF → QMD → PDF (automatic Quarto rendering)
python test_pdf_to_qmd.py input.pdf output.pdf

# The intermediate QMD is also saved as input.qmd
# You can edit it and re-render:
quarto render input.qmd --to pdf
```

## Comparison: Standard vs Quarto Edition

| Feature | Standard Edition | Quarto Edition |
|---------|------------------|----------------|
| PDF Input | Attempts direct PDF/UA | Converts via QMD |
| Alt Text Generation | ✅ All images | ✅ All images |
| Alt Text Embedding | ⚠️ Limited (not PAC-compliant) | ✅ Proper (via Quarto) |
| Structure Tags | ⚠️ Basic only | ✅ Full semantic (P, H1-H6, Figure) |
| Marked Content | ❌ Cannot create | ✅ Created by Quarto |
| Parent Trees | ❌ Cannot create | ✅ Created by Quarto |
| Table Extraction | ❌ Not preserved | ⚠️ May need manual fixes |
| Multi-column Layouts | ❌ Lost | ⚠️ May be scrambled |
| Output Format | PDF (basic tagging) | PDF (fully tagged via Quarto) |
| PAC Validation | ❌ Fails structure checks | ✅ Passes PDF/UA validation |
| Processing Time | ~10-30 seconds | ~1-2 minutes (includes rendering) |
| Requires Quarto | ❌ No | ✅ Yes |
| Best For | Quick metadata fixes | Complete accessibility |

## Limitations

Both approaches have limitations due to PDF being a presentation format:

1. **Table structure**: May be lost or garbled in extraction
2. **Multi-column layouts**: Reading order may be incorrect
3. **Complex positioning**: Sidebars, callouts may need repositioning
4. **Mathematical equations**: Only preserved if originally LaTeX

**Recommendation**: If you have the original source (Word, LaTeX, etc.), start from that instead of the PDF.

## When to Use Which Edition

### Use Standard Edition when:
- You only need to add metadata (title, language, bookmarks)
- You want to generate alt text descriptions for reference
- You're okay with manual PDF/UA tagging in Adobe Acrobat afterward

### Use Quarto Edition when:
- You need a truly accessible output (HTML or PDF)
- You want PAC validation to pass
- You're willing to render through Quarto
- You can manually fix any table/layout issues in the QMD

## Installation Requirements

Both editions require the same Python dependencies (already in requirements.txt):
- streamlit
- python-dotenv
- anthropic (Claude API)
- fitz (PyMuPDF)
- beautifulsoup4
- python-pptx
- markdown

For rendering QMD files, you also need:
- [Quarto](https://quarto.org/docs/get-started/) installed separately

## Future Enhancements

Possible improvements to the PDF→QMD converter:
1. Better table extraction (using tabula-py or camelot)
2. Multi-column layout detection
3. OCR integration for scanned PDFs
4. Batch processing with progress tracking
5. Image extraction to separate directory
