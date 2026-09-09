# AI Grader

A Streamlit app that grades **scanned paper quizzes/exams** with an LLM, end to end:

1. **Config** – set up the exam, roster, rubric, grounding materials, points, and curve.
2. **OCR & Split** – one scanned PDF (all students, 1..n pages each) is OCR'd by a
   vision model, split into per-student submissions, and matched to the roster.
3. **Grading** – each submission is graded in a **clean context window** against the
   rubric + grounding materials, per question. A curve and min/max clamp are applied.
4. **Download** – **every** submission is rendered and zipped: graded papers with a red
   grade report, and everything else with a hand-grading cover sheet.
   Files are named `last_name_first_name.pdf`.

The entire project (config + OCR + grades) is backed by a single **`state.json`** in a
working directory under `/tmp`, and can be saved/loaded at any time.

## No quiz is ever lost

Grading a class is a long batch against a flaky proxy, so the app assumes it will be
interrupted and is built so that an interruption never costs a student their paper.

- **OCR and grading checkpoint to `state.json` after every page and every paper.** A
  crash, disconnect, or proxy outage loses at most the one item in flight.
- **Both steps resume.** *Re-OCR failed pages* transcribes only the pages that errored;
  *Grade remaining* grades only papers that are ungraded or errored. Neither re-does
  work you already paid for.
- **The download includes every submission, always** — graded, failed, never graded, and
  never assigned to a student. Ungraded papers come out as their original scanned pages
  behind a cover sheet saying why they were not graded, with a blank score line, so you
  can finish by hand without dropping anyone.
- **An unreadable page never silently swallows the next student.** A page whose OCR
  fails always *starts* a new submission and is flagged for review. The two ways of
  guessing wrong are not symmetric: a spurious split is an extra unassigned submission
  you merge in one click, while a missed split folds one student into another's paper
  and returns a single grade covering two people. When the full transcription call
  fails, a second tiny call recovers just the split boundary, so a lost transcription
  never costs a boundary.
- **The split is cross-checked against the roster.** Fewer submissions than students is
  reported as an error on the OCR tab before you ever reach grading.
- **A paper is never given a score nobody read.** A submission whose OCR produced no text
  is routed to hand grading instead of being sent to the model, where an empty
  transcription would come back as a confident-looking zero.
- **The bundle reconciles against the scan and the roster**: pages belonging to no
  submission, roster students with no submission, and students holding more than one are
  all reported before you hand papers back. Pages in no submission are *exported* under
  `unaccounted_pages/`, not merely named — every page of the scan is in the ZIP.
- `state.json` is written atomically and the previous version is kept as
  `state.json.bak`, which **Load** falls back to if the main file is ever truncated.

## Setup

The `ai_grader` conda environment is already created. Configure the LLM in `.env`:

```
OPENAI_ENDPOINT=https://llm-api.arc.vt.edu/api/v1
OPENAI_APIKEY=sk-...
OPENAI_MODEL=thinkinglatest     # text model — grading
OPENAI_VISION_MODEL=Kimi-K3     # vision model — OCR only
OPENAI_MAX_INFLIGHT=3           # proxy's per-user concurrent request cap
```

> **`OPENAI_MAX_INFLIGHT` must not exceed what the proxy allows** (3 per user per
> model on ARC). Exceeding it does not queue — the proxy *rejects* the excess with
> HTTP 400 `concurrent session limit reached`, which reads exactly like a bad model
> name. Every rejected page is a page that never gets transcribed. The client now
> gates its own requests through a semaphore so no caller can push past the cap,
> and treats that 400 as throttling to be retried rather than a fatal misconfiguration.

(See `.env.example`. The API key can also be overridden in the Config tab.)

> **`OPENAI_VISION_MODEL` must name a genuinely multimodal model.** `Kimi-K3` is the
> multimodal model on ARC; `GLM-5.3` and `DeepSeek-V4-Flash` reject images with a 400.
> The `vision` alias no longer resolves at all (400 `Model not found`). OCR now
> preflights the model once before transcribing anything, so a bad name fails in a
> fraction of a second instead of after hundreds of doomed requests.

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
3. **Grading tab** – click **Grade *n* remaining**. Per-question scores, the curve
   summary, and per-student feedback appear; the curve is applied automatically. The
   button grades only what still needs it, so press it again to pick up after an
   interrupted run or to retry papers that errored. **Re-grade everything** discards
   existing grades and starts over. The results table lists *every* submission, including
   the ones that need hand grading.
4. **Download tab** – check the **Reconciliation** panel, then **Render & build ZIP** and
   **Download all submissions**. The ZIP contains:

   ```
   graded/           machine-graded papers + grade report
   needs_grading/    NOT graded — cover sheet (why, blank score line) + scanned pages
   unassigned/       no student matched — same cover sheet, named by starting scan page
   unaccounted_pages/  scan pages in no submission at all (absent when there are none)
   manifest.csv      one row per submission: file, status, student, pages, scores, note
   scores.csv        student + final score; blank where you still have to grade by hand
   reconciliation.txt  uncovered pages, students with no submission, duplicate students
   ```

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
  ocr.py               per-page vision OCR (resumable), eval splitting, anchors
  grading.py           constitutional grading prompt, per-question scoring, curve
  pdfutil.py           PDF text extraction, rasterization, grade + hand-grading reports
  export.py            the download bundle: every submission, manifest, reconciliation
```
