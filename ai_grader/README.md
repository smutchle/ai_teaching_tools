# AI Grader

A Streamlit app that grades **scanned paper quizzes/exams** with an LLM, end to end:

1. **Config** – set up the exam, roster, rubric, grounding materials, points, and curve.
2. **OCR & Split** – one scanned PDF (all students, 1..n pages each) is OCR'd by a
   vision model, split into per-student submissions, and matched to the roster.
3. **Grading** – each submission is graded in a **clean context window** against the
   rubric + grounding materials, per question. A curve and min/max clamp are applied.
4. **Download** – each student's graded PDF is rendered with **red annotations on the
   original scanned pages** (per-question score + comment, final score) and zipped.
   Files are named `last_name_first_name.pdf`.

The entire project (config + OCR + grades) is backed by a single **`state.json`** in a
working directory under `/tmp`, and can be saved/loaded at any time.

## Setup

The `ai_grader` conda environment is already created. Configure the LLM in `.env`:

```
OPENAI_ENDPOINT=https://llm-api.arc.vt.edu/api/v1
OPENAI_APIKEY=sk-...
OPENAI_MODEL=thinkinglatest     # text model — grading
OPENAI_VISION_MODEL=Kimi-K3     # vision model — OCR only
```

(See `.env.example`. The API key can also be overridden in the Config tab.)

> **`OPENAI_VISION_MODEL` must name a genuinely multimodal model.** On the ARC proxy
> the `vision` alias currently routes to `gpt-oss-120b`, which is text-only and
> *silently drops* the image rather than erroring — every page then comes back empty
> and the whole exam collapses into a single submission. `Kimi-K3` is the multimodal
> model there; `GLM-5.3` and `DeepSeek-V4-Flash` reject images with a 400. If most
> pages transcribe as empty, this is why — the OCR tab warns about it.

## Run

```bash
./run.sh
# or:
conda activate ai_grader && streamlit run app.py
```

## Workflow

1. **Config tab**
   - The working dir is auto-created in `/tmp`; edit the path + **Load** to reopen a saved project.
   - Upload the **roster CSV** (a single column of names, one per line, each
     written `"last_name, first_name"` in double quotes; a `name` header row is optional),
     the **exam PDF** (single file, all students), the **rubric & answers PDF** (single file),
     and any **grounding PDFs** (multiple).
   - Set **max/min points**, an optional **curve minimum average**, and grading instructions.
   - **Save project** writes `state.json`. **Reset** starts over (with confirmation).
2. **OCR & Split tab** – click **Perform OCR**, then review the scan page by page:
   - The **page image** is shown next to its controls; move with **◀ Prev / Next ▶**,
     the page slider, the **⏭ Next unassigned** button, or the per-submission jump
     buttons in the *All submissions* list at the bottom.
   - **This page starts a new submission** is the split boundary. It is pre-set from the
     vision model's Name-header detection — untick or tick it to merge or split, and the
     submissions rebuild immediately (assignments are kept).
   - Pick the **student** for the submission the current page belongs to. The dropdown is
     pre-filled by fuzzy-matching the handwritten name against the roster and left blank
     when the match is not confident; a submission with no student is never graded.
   - Pages that failed to OCR are called out explicitly at the top. Click **Save**.
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
  ocr.py               per-page vision OCR, eval splitting (hand-correctable), anchors
  grading.py           constitutional grading prompt, per-question scoring, curve
  pdfutil.py           PDF text extraction, rasterization, red annotation rendering
```
