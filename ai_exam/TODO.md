# TODO

Status tracker for the agentic exam-generator build. Status against `specifications/exam_agent_design.md` §10.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

---

## Handoff — pick up here

**Where we are:** End-to-end working. Phase 0–4 + the 5-page Streamlit UI all live and verified. On Sonnet HIGH + Haiku LOW the test corpus runs in **~3 minutes** wall-clock and produces a full Quarto export bundle (`exam.{pdf,docx,tex,qmd}`, `answer_key.*`, `instructor_notes.*`, `rubrics.*`, `exam_report.*`, `provenance.json`) plus an on-demand narrative PDF.

**To run from scratch:**
```bash
./run.sh                 # Streamlit UI on http://localhost:8550
# Pick the bundled test corpus (toggle on by default), click Start Run.
```
Or via CLI: `conda run --no-capture-output -n genai python run.py --pdf test_data/pchem_notes.pdf` (defaults to all-Ollama; pass `--high-provider anthropic --high-model claude-sonnet-4-6 --low-provider anthropic --low-model claude-haiku-4-5-20251001` to match the UI default).

**Latest verified run:** `runs/run_20260521_085319/` — Sonnet+Haiku, full Phase 0–4 + 4 epochs of Phase 3, 5 items / 21 pts surviving / 1 rejected, audit raised 2 exam-level objections (uncovered `clo_vant_hoff`, difficulty curve drift), all bundle artifacts produced with the latest rendering fixes (proper MCQ + multi-part lists across PDF/DOCX/HTML/LaTeX).

---

## Snapshot

| Layer | Status |
|---|---|
| Data models (Pydantic, ~30 types) | done |
| BaseAgent + retry-on-validation + per-attempt sidecars | done |
| All 9 LLM agents (SME, BA, IWS, LOA, Grounding, Accessibility, Adversarial Student, Psychometrician, Narrator) | done |
| Provider abstraction (Anthropic, ARC OpenAI-compat, Ollama) with per-tier routing | done |
| `Retriever` + `FakeRetriever` + `ChromaRetriever` + `OllamaEmbedder` + pypdf ingestion + heading-aware chunker | done |
| Event log (JSONL + per-call sidecars + Markdown formatter + filelock concurrent writes) | done |
| Adaptive theme extraction (single-pass + multi-batch + consolidate) | done |
| Moderator Phase 0–2 (themes/blueprint/CP1 → per-cell items/CP2) | done — parallel cells, LOA realignment fallback, per-cell safe wrapper |
| Moderator Phase 3 (parallel critique + epoch loop + accept/rebut/defer + re-verify) | done — batched critics, batched SME rebut, per-item safe wrapper |
| Moderator Phase 4 (audit + variants + Quarto export bundle) | done |
| `config.py` + per-tier `MODEL_REGISTRY` + `override_tiers` / `override_provider` | done |
| `run.py` CLI entry point with `--provider` / `--high-*` / `--low-*` flags | done |
| Streamlit UI (5 pages: Run / Job Monitor / Job Outputs / Personas / Documentation) | done — port 8550 |
| Run page: forms (Materials spec, Exam spec, Trade-off policy) + provider tier selectors + import/export config + subprocess launcher | done |
| Job Monitor page: Plotly swimlane timeline with click-to-drill, live polling, per-run + bulk delete | done |
| Job Outputs page: per-stem expanders with inline PDF previews + ZIP download + on-demand narrative generation | done |
| Personas page: edit + save/reset/reload `.md` files | done |
| Documentation page: agents markdown + pre-rendered Mermaid SVGs per phase | done |
| Post-hoc narrative (templater + Haiku polish + Quarto PDF/HTML) | done |
| Quarto bundle rendering (`_normalize_markdown`, MCQ + multi-part letter lists, fancy_lists) | done |
| Streamlit driver UI (pauses at CP1/CP2/CP3 for human approval) | not started — explicitly decided against in favor of kick-off-and-watch |
| SQLite persistence layer | not started — JSON snapshots adequate for current single-session UX |
| QTI / Common Cartridge export | not started — DOCX/PDF/LaTeX cover the dominant workflows |
| Streamlit per-item provenance viewer | not started |

---

## Open work, in priority order

1. **Scaling theme extraction to real textbooks.** The batcher (`SMEAgent.propose_themes`) splits and consolidates correctly, but the multi-batch path is unexercised because the test corpus (7 chunks) fits in one batch. Drop `theme_chunk_budget_chars` in the constructor or ingest a real 200+ page textbook to validate.
2. **Variant smoke test.** `test_data/exam_spec.json` has `accommodations_required: []` so the variant fan-out path never fires in the default run. Either add `extended_time` (or another kind) to the test spec, or write a focused unit test.
3. **Atomic sidecar writes.** `EventLog.write_call_io` writes JSON sidecars without write-to-tmp-then-rename. Under heavy parallelism a crashed Python process could leave a partial file. Low risk in practice; worth fixing for paranoid runs.
4. **Markdown transcript download** from the Job Monitor sidebar — `EventLog.to_markdown()` exists, just needs a download button.
5. **`priority_rank` consultation in Phase 3.** When two critics make conflicting demands on the same item (e.g., Accessibility says "remove this jargon", SME says "the jargon IS the construct"), the trade-off policy should pick a winner without round-tripping to SME twice. Today every objection is independent.
6. **Rebut-reassert escalation.** If a critic re-raises a previously-rebutted (item, category) pair across epochs, surface it at CP3 instead of leaving the objection open indefinitely.
7. **Anti-sycophancy programmatic enforcement.** Personas already discourage empty critique; no automatic re-prompt today.
8. **SQLite migration** — needed when cross-session resume becomes a requirement (currently single-session UI is fine).
9. **`requirements.txt`** for the project. All deps are in the shared `genai` env; pinning per-project may matter for distribution.
10. **Test suite.** No unit tests exist for the agent verbs. Mock provider + canned tool-use responses would catch the model-output regressions we keep hitting.

---

## Lessons from real-data debug

### The whack-a-mole season (resolved by the retry-on-validation loop)

Models routinely produce responses that don't match the Pydantic schema. We accumulated 5–7 bandaids before adding a principled retry mechanism:

| Symptom | Root cause | Bandaid (still in place) | Real fix |
|---|---|---|---|
| `themes` returned as stringified JSON | Opus single-key wrapping | `_validate_tool_input` Strategy B unwrap | covered by retry-on-validation |
| `updated_draft` stringified while sibling `rationale` is plain | Opus per-field stringification | `_recursive_unwrap` Strategy A | covered by retry |
| `supported_claims` stringified array with `\Delta` (invalid JSON escape) | Sonnet emitting raw LaTeX backslashes inside stringified strings | `_repair_json_escapes` | covered by retry |
| `EditResult.rationale` missing | Haiku terseness | default `""` in model | covered by retry |
| `ItemDraftList.items` missing | Haiku terseness | default `[]` in all wrapper-list models | covered by retry |
| One bad cell crashes whole Phase-2 gather | `asyncio.gather` propagates exceptions | `_safe_phase_2_cell` and `_safe_route_item_objections` wrappers | architectural — keep wrappers |

The retry-on-validation loop in `_invoke` (`max_attempts=2`) absorbs almost all of these on the second attempt by feeding the Pydantic error back to the model. The defaults + safe wrappers remain as belt-and-suspenders.

### Patterns observed (still relevant)

- **SME inflates Bloom level.** Mitigated by the LOA `suggest_realignment` fallback in `_phase_2_cell`. Recovers ~65% of historical Phase-2 rejections.
- **Anti-sycophancy framing works.** Rebut rates are high (~70%) and substantive — rejection rationales read like careful colleague feedback.
- **Post-edit re-verification is necessary.** SME edits occasionally drift items out of alignment; without IWS → LOA → Grounding re-check the item ships silently broken.

### Quarto rendering pitfalls (resolved)

Three issues compounded the rendering brittleness; each took a layered fix:

| Issue | Fix |
|---|---|
| Bullet lists glued to preceding paragraph | `_normalize_markdown` inserts blank lines before list items |
| `**(a)**` inline-bold sub-question markers render as text not list | regex strips inline formatting before list parsing |
| Pandoc's `fancy_lists` requires TWO spaces after capital-letter markers | renderer emits `A.  ` (two spaces) for MCQ options |
| MCQ + multi-part letter style inconsistent (`A.` vs `(a)`) | `_normalize_markdown` canonicalizes every letter marker to `A.  ` |
| SME embeds letter prefix inside option text → doubled letters | `_strip_option_letter_prefix` removes leading `[A-H][.)]\s+` |
| Mermaid CDN rendering unreliable in Streamlit (dagre layout failures) | Pre-render diagrams to SVG via `mmdc` at design time; inline the SVG |

### Concurrency pitfalls (resolved)

- Single-GPU Ollama serializes requests internally → 8 parallel HTTP calls time out → fix: per-provider semaphores (`Ollama=1`, `ARC=4`, `Anthropic=8`)
- Ollama thinking models burn 4k tokens on reasoning before producing JSON → fix: `max_tokens_floor=16384` for Ollama
- Pipeline crash → zombie python process → `os.kill(pid, 0)` reports alive → UI says "running" forever → fix: `is_pid_alive` reads `/proc/<pid>/status` and rejects `State: Z`

---

## Test inputs

- `test_data/course_spec.json` — 7 CLOs (apply + analyze mix), 7 weighted topics, principles
- `test_data/exam_spec.json` — quiz, 30 pts, 40 min, 4 MCQ + 2 short + 2 problem
- `test_data/policy.json` — content_fidelity > cognitive_alignment > accessibility > discrimination > brevity, max_epochs=4
- `test_data/pchem_notes.pdf` (4 pages → 7 Chroma chunks)
