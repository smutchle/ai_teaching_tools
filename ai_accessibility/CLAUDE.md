# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WCAG 2.1 AA Accessibility Converter - A Streamlit application that converts documents (HTML, Markdown, LaTeX, PDF, PowerPoint, Quarto) to WCAG 2.1 Level AA accessible forms using AI-powered accessibility fixes.

## Environment Setup

**Conda Environment**: This project uses the `genai` conda environment.

```bash
# Activate environment
conda activate genai

# Install dependencies (if needed)
pip install -r requirements.txt
```

**Environment Variables**: Create a `.env` file with:
```
CLAUDE_API_KEY=your_api_key_here
CLAUDE_MODEL=claude-sonnet-4-5
```

## Common Commands

### Running the Application

**Two versions available:**

1. **Standard Version** ([ai_access_app.py](ai_access_app.py)) - Attempts direct PDF/UA tagging
2. **Quarto Edition** ([ai_access_app_qmd.py](ai_access_app_qmd.py)) - Converts PDFs to Quarto Markdown

```bash
# Run standard Streamlit app (default port 8501)
streamlit run ai_access_app.py

# Run Quarto edition (converts PDFs to QMD)
streamlit run ai_access_app_qmd.py

# Run in background on port 9997 (uses conda genai)
./run_in_background.sh

# Stop background process on port 9997
lsof -ti:9997 | xargs kill -9
```

### Testing

```bash
# Test PDF → PDF conversion (direct approach)
python test_pdf_conversion.py <input.pdf> [output.pdf]

# Test PDF → QMD conversion (Quarto approach)
python test_pdf_to_qmd.py <input.pdf> [output.qmd]

# Test with specific components
python test_single_image.py
python test_language_fix.py
python test_streamlit_download.py
python test_raw_response.py
```

**Note**: Test scripts are standalone scripts demonstrating specific functionality, not a formal test suite.

## Architecture

### Core Components

**Processor Pattern**: All document processors inherit from `BaseProcessor` (abstract base class):

```
processors/
├── base.py                    # BaseProcessor (ABC)
├── html_processor.py          # HTML/HTM files
├── markdown_processor.py      # Markdown files
├── qmd_processor.py           # Quarto Markdown files
├── latex_processor.py         # LaTeX files
├── pdf_processor.py           # PDF files (direct PDF/UA approach)
├── pdf_to_qmd_processor.py    # PDF→QMD converter (Quarto approach)
├── pdf_advanced_tagging.py    # PDF/UA tagging utilities
└── pptx_processor.py          # PowerPoint files
```

**Each processor**:
- Implements `process(content: bytes, filename: str) -> bytes`
- Creates an `AccessibilityReport` tracking fixes and issues
- Uses `ClaudeClient` for AI-powered features
- Returns processed content with WCAG 2.1 AA fixes applied

**Utilities**:

```
utils/
├── claude_client.py       # AI operations via Claude API
├── accessibility.py       # AccessibilityReport, AccessibilityChecker
└── __init__.py
```

### ClaudeClient Operations

The `ClaudeClient` handles all AI-powered accessibility features:

- `generate_alt_text()` - WCAG-compliant alt text from images
- `analyze_heading_structure()` - Detects heading hierarchy issues
- `improve_link_text()` - Fixes generic link text ("click here", etc.)
- `generate_table_caption()` - Descriptive table captions
- `describe_complex_image()` - Alt text + long descriptions for complex images
- `analyze_document_accessibility()` - Comprehensive WCAG analysis

### Accessibility Report Structure

`AccessibilityReport` tracks:
- **issues**: List of `AccessibilityIssue` (WCAG criterion, severity, description, location, suggestion)
- **fixes_applied**: List of automated fixes performed
- **warnings**: General warnings about document structure
- **Severity levels**: ERROR, WARNING, INFO

### File Processing Flow

1. Main app ([ai_access_app.py](ai_access_app.py)) receives uploaded file
2. File extension determines processor via `FILE_PROCESSORS` dict
3. Processor applies WCAG fixes in `process()` method:
   - Language attributes
   - Document titles
   - Heading hierarchy fixes
   - Alt text generation (AI)
   - Table accessibility (headers, captions)
   - Link text improvements (AI)
   - Skip links, ARIA landmarks
   - Form label associations
4. Returns processed content + accessibility report
5. UI displays report and offers download

## WCAG Criteria Addressed

The tool addresses these WCAG 2.1 Level AA criteria:

- **1.1.1** - Non-text Content (alt text for images)
- **1.3.1** - Info and Relationships (proper structure, tables)
- **1.3.2** - Meaningful Sequence (reading order)
- **2.4.1** - Bypass Blocks (skip navigation)
- **2.4.2** - Page Titled (document titles)
- **2.4.4** - Link Purpose (descriptive links)
- **2.4.6** - Headings and Labels (clear headings)
- **3.1.1** - Language of Page (language declaration)
- **4.1.2** - Name, Role, Value (ARIA labels, form labels)

## Adding New Processors

To add support for a new file format:

1. Create new processor in `processors/` inheriting from `BaseProcessor`
2. Implement required methods: `process()`, `get_file_extension()`
3. Add to `processors/__init__.py` exports
4. Register in [ai_access_app.py](ai_access_app.py) `FILE_PROCESSORS` dict with file extension

Example structure:
```python
class NewFormatProcessor(BaseProcessor):
    def get_file_extension(self) -> str:
        return ".ext"

    def process(self, content: bytes, filename: str = "") -> bytes:
        self.reset_report()
        # Parse content
        # Apply WCAG fixes using self.claude_client
        # Track fixes with self.report.add_fix()
        # Track issues with self.report.add_issue()
        # Return processed bytes
```

## Key Implementation Details

**Generic Link Text Detection**: The system flags and fixes common generic link texts:
- "click here", "here", "read more", "learn more", "more", "link", "this link", "info", "details"
- Uses AI to derive descriptive text from URL and surrounding context

**Heading Hierarchy Fixes**:
- Detects skipped heading levels (e.g., h1 → h3)
- Ensures one h1 per document
- Uses `AccessibilityChecker.fix_heading_hierarchy()` for corrections

**Image Alt Text**:
- Analyzes actual image data when available
- Uses surrounding text context when image data unavailable
- Marks decorative images with empty alt=""
- Complex images get both alt text and long descriptions

**PDF Processing - Two Approaches**:

1. **Direct PDF/UA Approach** ([pdf_processor.py](processors/pdf_processor.py)):
   - Generates AI alt text for ALL images
   - Attempts to embed alt text in PDF structure
   - Creates basic StructTreeRoot
   - Sets document metadata and language
   - **Limitation**: PyMuPDF cannot create full PDF/UA structure trees (marked content sequences, parent trees, semantic tagging)
   - Alt text is generated but may not be embedded in PAC-compliant structure

2. **PDF→QMD→PDF Approach** ([pdf_to_qmd_processor.py](processors/pdf_to_qmd_processor.py)) - **Recommended**:
   - Extracts text and images from PDF
   - Detects headings by font size
   - Generates AI alt text for all images
   - Converts to Quarto Markdown format
   - **Automatically renders QMD to PDF using Quarto** (creates properly tagged PDFs)
   - Returns accessible PDF with full PDF/UA structure
   - Intermediate QMD available via `get_qmd_content()` for editing
   - **Requires**: Quarto installed (https://quarto.org)
   - **Limitation**: PDF→QMD conversion is lossy (tables, multi-column layouts may need manual fixes)

**Which approach to use:**
- For creating **accessible PDFs** that pass PAC validation: Use Quarto Edition (PDF→QMD→PDF automatic)
- For creating **accessible HTML** from PDFs: Manually render the QMD with `quarto render file.qmd --to html`
- For quick metadata fixes only: Use standard edition, but understand structure tree limitations
- **Note**: Quarto Edition requires Quarto to be installed on the system
