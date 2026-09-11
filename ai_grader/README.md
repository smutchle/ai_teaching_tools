# AI Grader

A Streamlit app that grades **scanned paper quizzes/exams** with an LLM, end to end:

It is **one page and one button**: fill in the exam, drop in the scan and the rubric,
press **🚀 Scan & grade everything**, and the app runs all four phases itself, reporting
where it is throughout:

1. **Scan & split** – one scanned PDF (all students, 1..n pages each) is read by a
   vision model, split into per-student submissions, and matched to the roster.
2. **Check the scan** – the split is cross-checked against the scan and the roster.
   This is the one place the app stops and asks: see *Intervening*, below.
3. **Grade** – each submission is graded in a **clean context window** against the
   rubric + grounding materials, per question. A curve and min/max clamp are applied.
4. **Package** – **every** submission is rendered and zipped: graded papers with a red
   grade report, and everything else with a hand-grading cover sheet.
   Files are named `last_name_first_name.pdf`.

The ARC API key is optional and lives in a collapsed panel in the sidebar — the app
reads `OPENAI_APIKEY` from `.env` and almost nobody needs to type one.

The entire project (config + OCR + grades) is backed by a single **`state.json`** in its
own project directory, and can be saved/reopened at any time by its **project code**.
Several instructors can use one server at once — see *Multiple instructors at once*.

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
  reported in the Check the scan panel before you ever reach grading.
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
OPENAI_MODEL=vt-arc-llm         # text model — grading
OPENAI_VISION_MODEL=vt-arc-llm  # vision model — OCR only
OPENAI_MAX_INFLIGHT=3           # proxy's per-user concurrent request cap
```

> **`OPENAI_MAX_INFLIGHT` must not exceed what the proxy allows** (3 per user per
> model on ARC). Exceeding it does not queue — the proxy *rejects* the excess with
> HTTP 400 `concurrent session limit reached`, which reads exactly like a bad model
> name. Every rejected page is a page that never gets transcribed. The client now
> gates its own requests through a semaphore so no caller can push past the cap,
> and treats that 400 as throttling to be retried rather than a fatal misconfiguration.

(See `.env.example`. The API key can also be overridden in the sidebar's
collapsed **API key** panel, which is optional.)

> **Both model settings are `vt-arc-llm`.** ARC serves one alias, which does reasoning
> and vision alike, so there is no model to choose. The two settings are kept separate
> so OCR and grading can be pointed at different models again without a code change.
> Name the alias, not whatever model currently sits behind it — that changes, and a
> stale name fails with a 400 that reads like an auth error. OCR preflights the model
> once before transcribing anything, so a bad name fails in a fraction of a second
> instead of after hundreds of doomed requests.

## Run

```bash
./run.sh
# or:
conda activate ai_grader && streamlit run app.py
```

## Workflow

1. **Set up the exam** (sections 1 and 2 of the page)
   - A project directory is created for you; the sidebar shows its **project code**, which
     is what reopens it later (paste it into *Open another project*). **Save** and
     **Start a new project** are there too.
   - Upload the **roster CSV** (a single column of names, one per line, each
     written `"last_name, first_name"` in double quotes; a `name` header row is optional),
     the **exam PDF** (single file, all students), the **rubric & answers PDF** (single file),
     and any **grounding PDFs** (multiple).
   - Set **max/min points**, an optional **curve minimum average**, and grading instructions.
2. **Press 🚀 Scan & grade everything** (section 3). That is the whole run: scan, check,
   grade, curve, package. A four-phase stepper, a progress bar with a running count and
   an ETA, and a live run log show what it is doing at every moment. *Run only part of it*
   holds the narrower buttons — re-scan just the failed pages, grade only the remaining
   papers, re-grade everything, rebuild the download.
3. **Check the scan** (section 4) – shown after every run, and the one thing that can stop
   the pipeline. See *Intervening*, below. To review the scan page by page:
   - The **page image** is shown next to its controls; move with **◀ Prev / Next ▶**,
     the page slider, the **⏭ Next unassigned** button, or the per-submission jump
     buttons in the *All submissions* list at the bottom.
   - **This page starts a new submission** is the split boundary. It is pre-set from the
     vision model's Name-header detection — untick or tick it to merge or split, and the
     submissions rebuild immediately (assignments are kept).
   - Pick the **student** for the submission the current page belongs to. The dropdown is
     pre-filled by fuzzy-matching the handwritten name against the roster and left blank
     when the match is not confident; a submission with no student is never graded.
   - The **transcription** for the current page is editable. Type over what the model got
     wrong — or type in a page it could not read at all — and press **Apply this
     transcription**; what you type is what gets graded, and a page you transcribe by hand
     stops counting as an unread page.
4. **Grades** (section 5) – per-question scores, the curve summary, and per-student
   feedback. The results table lists *every* submission, including the ones that need hand
   grading.
5. **Download** (section 6) – check the **Reconciliation** panel, then
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

## Multiple instructors at once

One server handles many simultaneous users. Each browser session is independent,
and three things keep them that way:

**Projects are separate directories, reached only by code.** Every session gets its own
project directory named by a random **project code** (shown in the sidebar). All projects
live under one workspace root — `$AI_GRADER_WORKSPACES`, or `/tmp/ai_grader_workspaces`
by default, created `0700`. *Open another project* takes a code, not a path: anything that
would name a file outside that root is refused, so no session can reach another session's
`state.json` (or anything else on the server) by typing a path.

Set `AI_GRADER_WORKSPACES` to a real volume on a shared server — `/tmp` is cleared on
reboot, and nothing here deletes old projects for you.

**One session at a time may write to a project.** Opening a project claims a lock
(`session.lock`) that every checkpoint refreshes, so even a long scan keeps it alive.
A second session opening the same code is refused, with a deliberate **Take it over
anyway** if the first session was abandoned; a lock idle for 15 minutes goes stale and is
reclaimed without asking. A session whose project has been taken over is told so and is
blocked from running or saving over the new holder. Two sessions writing one `state.json`
would interleave saves and silently lose pages, grades, and student assignments.

**The proxy's concurrency cap is enforced across sessions.** ARC limits in-flight requests
per API key, and answers the excess with a 400 that is indistinguishable from a bad model
name. The gate that respects that limit is therefore process-wide and keyed by API key, not
per client object — five simultaneous instructors on the same key still put at most
`OPENAI_MAX_INFLIGHT` requests in flight between them, and queue rather than collect
errors. The sidebar shows the live count. Sessions using a different key (via the optional
override) get their own budget, matching how the proxy counts.

> **No authentication.** Separation is by unguessable project code, not by login: anyone
> who can reach the port can start a session, and a shared code is a shared project. On an
> open network, put the app behind your own SSO/reverse proxy, or add Streamlit's built-in
> OIDC login (`st.login()`).

## Intervening when the scan cannot be trusted

A wrong split is the one error that is both invisible and expensive: a missed boundary
silently merges two students into one grade. So the pipeline stops before grading — it
never partially grades a bad scan — when any of these is true:

* a page could not be read at all;
* a page's split boundary is a guess rather than a reading;
* half or more of the pages transcribed to nothing (usually a non-multimodal vision model);
* there are fewer submissions than roster students;
* no submission matched a roster student.

When it stops, the **Check the scan** panel names every problem, shows **what the model
actually replied** for each failed page (raw, before JSON parsing — which is how
"I'm unable to view the image" gets caught), and drops you on the first page that needs
attention. Fix the split, the transcription or the student assignment there, and the
problems clear as you go. Then either **🔁 Re-scan the failed pages, then grade**, or
**▶️ Continue — grade & package** to proceed with the warnings outstanding. Nothing is
dropped either way: ungraded papers are still exported with a hand-grading cover sheet.

## Curve

If the class's raw average is below the target minimum average, an integer number of points
(`ceil(target − actual)`) is added to every paper, then each score is clamped to
`[min_points, max_points]`. No fractional points.

## Layout

```
app.py                 Streamlit UI (one page, one button) + pipeline runner
grader/
  llm.py               OpenAI-compatible client (text + vision), resilient JSON parsing,
                       process-wide per-key concurrency gate
  state.py             JSON-backed state, project codes, workspace confinement, locking
  roster.py            roster CSV loading + fuzzy name matching
  ocr.py               per-page vision OCR (resumable), eval splitting, anchors
  grading.py           constitutional grading prompt, per-question scoring, curve
  pdfutil.py           PDF text extraction, rasterization, grade + hand-grading reports
  export.py            the download bundle: every submission, manifest, reconciliation
```
