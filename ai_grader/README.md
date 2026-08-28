# AI Grader

A Streamlit app that grades **scanned paper quizzes/exams** with an LLM, end to end:

1. **Config** – set up the exam, roster, rubric, grounding materials, points, and curve.
2. **OCR & Split** – one scanned PDF (all students, 1..n pages each) is OCR'd by a
   vision model, split into per-student submissions, and matched to the roster.
3. **Grading** – each submission is graded in a **clean context window** against the
   rubric + grounding materials, per question. A curve and min/max clamp are applied.
4. **Download** – each student's graded PDF is rendered with **red annotations on the
   original scanned pages** (per-question score + comment, final score) and zipped.
   Files are named `last_name_first_name_studentid.pdf`.

The entire project (config + OCR + grades) is backed by a single **`state.json`** in a
working directory under `/tmp`, and can be saved/loaded at any time.

## Setup

The `ai_grader` conda environment is already created. Configure the LLM in `.env`:

```
OPENAI_ENDPOINT=https://llm-api.arc.vt.edu/api/v1
OPENAI_APIKEY=sk-...
OPENAI_MODEL=thinkinglatest     # text model — grading
OPENAI_VISION_MODEL=vision      # vision model — OCR only
```

(See `.env.example`. The API key can also be overridden in the Config tab.)

## Run

```bash
./run.sh
# or:
conda activate ai_grader && streamlit run app.py
```

## Workflow

1. **Config tab**
   - The working dir is auto-created in `/tmp`; edit the path + **Load** to reopen a saved project.
   - Upload the **roster CSV** (columns `last_name, first_name, student_id, email`, any order),
     the **exam PDF** (single file, all students), the **rubric & answers PDF** (single file),
     and any **grounding PDFs** (multiple).
   - Set **max/min points**, an optional **curve minimum average**, and grading instructions.
   - **Save project** writes `state.json`. **Reset** starts over (with confirmation).
2. **OCR & Split tab** – click **Perform OCR**. Review each submission, assign a student
   from the dropdown (required; pre-filled when a confident match is found), expand the OCR
   markdown to verify. Click **Save**.
3. **Grading tab** – click **Grade all**. Per-question scores, the curve summary, and
   per-student feedback appear. The curve is applied automatically after grading.
4. **Download tab** – **Render & build ZIP**, then **Download all graded PDFs**.

## Curve

If the class's raw average is below the target minimum average, an integer number of points
(`ceil(target − actual)`) is added to every paper, then each score is clamped to
`[min_points, max_points]`. No fractional points.

## Layout

```
app.py                 Streamlit UI (4 tabs)
grader/
  llm.py               OpenAI-compatible client (text + vision), resilient JSON parsing
  state.py             JSON-backed state, working-dir management
  roster.py            roster CSV loading + fuzzy name matching
  ocr.py               per-page vision OCR, eval splitting, anchors
  grading.py           constitutional grading prompt, per-question scoring, curve
  pdfutil.py           PDF text extraction, rasterization, red annotation rendering
```
