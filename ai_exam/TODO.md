# TODO

Status tracker for the agentic exam-generator build. Organized against the **Build Order** in `exam_agent_design.md` §10 — items map back to the original plan so you can see what is on-spec, what has slipped, and what is still ahead.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

---

## Handoff — pick up here

**Where we are:** Phase 0–4 are live. Phase 4 (audit + variants + Quarto export) and Phase 2/3 parallelism landed in this session. CLI entry point: `python run.py --pdf test_data/pchem_notes.pdf [--max-epochs N] [--skip-phase-3] [--skip-phase-4]`. Outputs land under `runs/run_<ts>/` with per-phase JSON snapshots, `events/events.jsonl`, per-call sidecars, `transcript.md`, and (Phase 4) an `exam_bundle/` with PDF + DOCX + LaTeX + provenance.

**To re-run from scratch:** activate the `genai` conda env, ensure `.env` has `ANTHROPIC_API_KEY`, ensure Ollama is up at `localhost:11434` with `nomic-embed-text-v2-moe:latest`, ensure `quarto` is on PATH (`which quarto` → e.g. `/opt/quarto/bin/quarto`), then `conda run --no-capture-output -n genai python run.py --pdf test_data/pchem_notes.pdf --max-epochs 1`.

**Expected speedup from parallelism (not yet measured on real corpus):** the 113-call Phase 3 epoch was fully sequential. The critique pass (21 Sonnet calls) now runs in parallel; the routing pass parallelizes across items (within an item objections stay sequential because edits mutate the item); Phase 2 cells run in parallel. Wall-clock should drop substantially even at the same call count.

**Most useful next pieces (in order of payoff):**
1. **Validate the ARC + batching wins on real corpus.** Provider abstraction landed (`providers.py`), default route is `arc/gpt-oss-120b` (free via VT ARC). Batched SME `rebut_objections` (1 call per item × non-critical objections, instead of 1 per objection) and batched critic `critique_batch` (1 call per critic, not 1 per critic×item). Old serial call count on test corpus = 131; expected new = roughly 50-65. To swap any agent back to Claude, edit `config.MODEL_REGISTRY` (e.g. `"sme": _ANTHROPIC_OPUS`).
2. **LOA `suggest_realignment` fallback in Phase 2** — biggest single quality lift. SME bloom-inflation is real; LOA `suggest_realignment` already exists as a verb but is never called. Wire it: when LOA rejects for `bloom_level_mismatch`, ask LOA for a suggestion, apply `remap_bloom` automatically. Recovers ~3 items per run that currently get dropped.
3. **Streamlit UI** (you asked to be told before any UI work begins). The reader-side recipe for the chat-style transcript view is in `TODO.md` §7.
4. Phase 3 polish: rebut-reassert escalation tracking, `priority_rank` consultation for cross-objection conflicts.
5. Variant smoke test against real data: `test_data/exam_spec.json` currently has `accommodations_required: []` so the variant path is unexercised in the default run. Either add a kind to that spec for verification or write a unit test.
6. Adaptive theme extraction landed (`SMEAgent.propose_themes`) — splits large corpora into ~80k-token batches and consolidates. Unit-tested for the splitter; full multi-batch path unexercised because the test corpus (7 chunks) hits the single-pass path. To exercise, lower the constructor's `theme_chunk_budget_chars` or ingest a real textbook.

**What you should NOT remove without thinking:**
- `_validate_tool_input` in `agents/base.py` — the four-strategy unwrap is load-bearing; Opus 4.7 stringifies nested fields unpredictably. Removing it will break Phase 3 within the first SME edit.
- The `temperature` parameter is intentionally absent from `BaseAgent._invoke` — Opus 4.7 rejects it as deprecated.
- Per-cell loop's top-of-loop break check + zero-target short-circuit in `_phase_2_cell` — guards against the off-by-one when BA produces target=0 cells.
- `self._item_counter_lock` in `Moderator` — Phase 2 cells run in parallel and the item id counter is the one piece of cross-worker shared state. Without the lock, item ids could collide.
- `_route_item_objections` is per-item by design — never collapse it back into a flat `for objection in objections_open` loop. The per-item grouping is what makes the parallel fan-out safe (same-item edits stay sequential).

**Latest run:** `runs/run_20260520_142951/` — pre-parallelism baseline. Phase 2: 7 items / 25 pts → Phase 3 (1 epoch): 6 REFINED items / 22 pts / 38 open objections / 1 rejection. Phase 4 unverified on a fresh run; smoke-test against this draft + a stub `ExamAudit` produced all 16 expected files (qmd + pdf + docx + tex × 4 documents, plus provenance.json), zero Quarto failures.

---

## Snapshot

| Layer | Status |
|---|---|
| Data models (Pydantic) | done |
| Persona-loading base agent (with prompt caching, forced tool use) | done |
| All 7 LLM agents (SME, BA, LOA, IWS, Accessibility, Psychometrician, Adversarial Student) | done |
| `Retriever` Protocol + `FakeRetriever` | done |
| Event log (JSONL + sidecar full-I/O + Markdown formatter) | done |
| Real retrieval (Ollama embedder + ChromaRetriever + pypdf ingestion) | done |
| Grounding Verifier | done |
| Moderator Phase 0–2 (intake → themes/blueprint/CP1 → per-cell items/CP2) | done, real-data verified |
| Moderator Phase 3 (parallel critique + epoch loop + accept/rebut/defer + re-verify) | done, real-data verified (1 epoch); parallelism not yet measured on real corpus |
| Moderator Phase 4 (audit + variants + Quarto export bundle) | done, smoke-tested against prior draft + stub audit |
| `config.py` + `.env` + per-agent model registry | done |
| `run.py` CLI entry point | done |
| SQLite persistence layer | not started |
| Streamlit UI (4 pages: Run / Transcript / Bundle / Personas) | done — port 8550, `./run.sh` / `./run_in_background.sh` |
| Streamlit chat transcript view + download buttons | done (chat bubbles, live polling, sidecar drill-down, ZIP bundle download) |
| Streamlit Run page (forms + kick-off + subprocess) | done — full forms for CourseSpec / ExamSpec / TradeOffPolicy, dynamic CLO + Topic lists, launches `python run.py` via Popen with detached session |
| Streamlit driver UI (pauses at CP1/CP2/CP3 for human approval) | not started — design decision: kick-off-and-watch, no human-in-the-loop checkpoints |
| Export bundle (DOCX/PDF/LaTeX/JSON) | done via Quarto (`export/`); QTI/Common Cartridge not started |

---

## §10 Build Order — mapped

### 1. Data models + SQLite schema + Streamlit upload screen

- [x] Pydantic data models in `models.py` (~30 types covering Phase 0 through Phase 4 outputs)
- [ ] SQLite schema (one row per item version, per objection, per provenance event — resumable across sessions per design §7). Today we use per-phase JSON snapshots; SQLite migration when multi-session resume matters.
- [ ] Streamlit upload screen (materials drop, CourseSpec form, ExamSpec form, trade-off sliders)

### 2. Ingestion + retrieval

- [x] `Retriever` Protocol (`retrieval/protocol.py`)
- [x] `FakeRetriever` (in-memory token-overlap, deterministic, for tests)
- [x] `OllamaEmbedder` (httpx → `localhost:11434/api/embed`, batched, nomic-embed-text-v2-moe)
- [x] Heading-aware chunker (`retrieval/chunking.py`)
- [x] PDF ingestion (`retrieval/ingestion.py`: pypdf → chunks → embed → Chroma upsert; deterministic chunk ids for idempotent re-ingest)
- [x] `ChromaRetriever` (persistent collection, cosine similarity, prior-exam default-exclude)
- [x] Prior-exam tagging at ingestion time (`Chunk.is_prior_exam=True`); retriever default-excludes them
- [x] Grounding Verifier (`agents/grounding_verifier.py` + persona) — tuned to distinguish stem-provided inputs (no chunk backing needed) from knowledge claims (must be in chunks)
- [ ] Concrete retrievers beyond Chroma (LanceDB, GraphRAG) — deferred; Chroma is adequate for the foreseeable scale

### 3. SME + Blueprint Architect through Checkpoint 1

- [x] `BaseAgent` with persona loading, forced-tool-use `_invoke`, system-prompt caching, event logging
- [x] `SMEAgent` (`propose_themes`, `propose_items`, `edit_item`, `rebut_objection`, `gather_context`)
- [x] `BlueprintArchitectAgent` (`propose_blueprint`, `revise_blueprint`)
- [x] Live-API smoke: SME extracts themes from pchem_notes.pdf; BA produces an 8-cell blueprint; round-trip works
- [x] Phase 1 pipeline in `Moderator.run_through_checkpoint_2` (themes → blueprint → CP1 auto-approve + snapshot)
- [ ] Streamlit Blueprint Review screen (Checkpoint 1: matrix view, editable cells, approve/edit)
- [ ] Interactive mode in CLI (`--interactive` flag pauses for stdin at each checkpoint)

### 4. Item generation loop through Checkpoint 2

- [x] `ItemWritingSpecialistAgent` (`cleanup`, `critique`, `propose_edit`)
- [x] `LearningOutcomesAlignmentAgent` (`verify_alignment`, `critique`, `suggest_realignment`)
- [x] Phase-2 pipeline in `Moderator._phase_2_cell`: SME.propose_items (overgenerate 1.5×) → IWS.cleanup → promote `ItemDraft`→`Item` (id, status, provenance) → LOA.verify_alignment → Grounding chunk lookup + Grounding.verify → accept if all pass, reject with rationale otherwise. Per-cell break when target_item_count reached.
- [x] Grounding Verifier integrated; missing `chunk_id` in `source_refs` auto-rejects without an LLM call
- [x] Per-phase JSON snapshots written to `outputs_dir/` (themes, blueprint, items, exam_draft)
- [x] CP2 auto-approve with summary in checkpoint event
- [ ] LOA `suggest_realignment` fallback in Phase 2: when LOA rejects for `bloom_level_mismatch`, optionally ask for a realignment suggestion and apply it (avoids losing items to SME bloom-inflation, which is a real failure pattern — see "Lessons" below)
- [ ] Phase-2 backfill: when a cell ends with fewer than `target_item_count` accepted, re-call SME with feedback or escalate at CP2
- [ ] Streamlit Item Bank Review screen (Checkpoint 2: item cards with provenance, accept/reject/edit, filter by CLO/Bloom/topic)

### 5. Critic agents

- [x] `AccessibilityExpertAgent` (`critique`, `propose_edit`, `generate_variant`)
- [x] `AdversarialStudentAgent` (`attempt_solve`, `critique`) — with answer-leaking field redaction (`_student_view`)
- [x] `PsychometricianAgent` (`estimate_difficulty`, `critique`, `audit_exam`)
- [ ] Live-test redaction once orchestrated end-to-end (verify no leakage path exists in real call flow, not just in unit test)
- [ ] Rule-based tools the design doc mentions but we have not built: item-flaw checklist (IWS), readability scorer (Accessibility), jargon detector (Accessibility), Bloom verb classifier (LOA). These are listed in §3 of the design doc but personas currently encode them as guidance rather than callable tools. Decide whether to build any as actual functions or leave as persona discipline.

### 6. Moderator + trade-off policy engine

- [x] `Moderator` deterministic router (not an LLM): Phase 0–3 state machine in place
- [x] `TradeOffPolicy` data type (`moderator/policy.py`): priority ranking over 5 dimensions + max_epochs + convergence rule. Loaded by Moderator; `max_epochs` is consulted (overridable via `--max-epochs`); `priority_rank` not yet consulted (see below).
- [x] Promotion logic: `ItemDraft` → `Item` (assign id, status=draft, append SME-proposed + IWS-edited provenance); `ObjectionDraft` → `Objection` (uuid id, agent stamp from critic PERSONA_NAME)
- [x] Provenance append on every state change (objected, edited, accepted, rejected, resolved with REBUT/DEFER detail)
- [x] Phase 3 epoch loop in `Moderator.run_through_checkpoint_3`: sequential critique by Accessibility + Adversarial Student + Psychometrician on every non-rejected item
- [x] Phase 3 routing: critical → force SME edit + re-verify (IWS cleanup → LOA → Grounding); non-critical → `SME.rebut_objection` then ACCEPT (edit + re-verify) / REBUT (log, objection stays open) / DEFER (log, stays open)
- [x] Post-edit re-verification: after every SME edit, run IWS cleanup → LOA verify → Grounding verify. Failure rejects the item with provenance.
- [x] Convergence detection: count critical+high in `objections_open` at end of epoch; exit if zero
- [x] `EpochMetrics` snapshot per epoch (`phase_3_epoch_N.json`) + final snapshot (`phase_3_final_draft.json`)
- [ ] Anti-sycophancy enforcement: re-prompt critics whose epoch-1 critique is empty (per design §3). Personas already discourage empty critique; no programmatic re-prompt yet.
- [ ] `priority_rank` consultation: pairwise objection conflict resolution. Today every objection is routed to SME independently; when two critics make conflicting demands on the same item (e.g., Accessibility says "remove this jargon", SME says "the jargon IS the construct"), the trade-off policy should pick a winner without asking SME twice.
- [ ] Rebut-reassert escalation: if a critic re-raises a previously-rebutted (item, category) pair across epochs, escalate to faculty at CP3. Currently both objections just stay open; convergence is the only stopping condition.
- [x] **Parallelism for the critique pass and routing pass** — `gather_sync` in `moderator/parallel.py` runs the 3×N critique calls in parallel; the routing pass groups objections by item.id and dispatches one worker per item (within-item objections stay sequential since edits mutate the item). Phase 2 cells also run in parallel. Uses `asyncio.to_thread` over the sync Anthropic SDK rather than `AsyncAnthropic` — avoids doubling the agent verb surface.
- [x] Exam-level Psychometrician audit at end of Phase 3 (`audit_exam` → `ExamAudit`) — now called by `Moderator.run_phase_4`. Report-only: surfaces exam-level objections in `phase_4_audit.json` and the rendered `exam_report.pdf` but does not auto-remediate.

### 7. Refinement live view UI

- [x] `events.py` infrastructure (AgentEvent, EventLog, EventKind, JSONL + sidecar, Markdown formatter, filelock-safe concurrent appends)
- [x] BaseAgent emits invocation_started / invocation_completed / invocation_failed automatically on every `_invoke`; verb name captured from call stack; tokens + duration recorded; failure path preserves original exception
- [ ] Moderator emits routing_decision / policy_applied / checkpoint_reached / provenance_appended into the same log (depends on Moderator being built)
- [x] Streamlit Refinement Live View — `ui/transcript_view.py` renders each event as `st.chat_message`, polls via `st.fragment(run_every=2.0)` when "Live" toggle is on.
- [x] Streamlit sidebar download buttons for export bundle artifacts (`ui/bundle_view.py`) plus inline PDF preview for `exam.pdf`.
- [x] Event-detail drawer: every `INVOCATION_COMPLETED` bubble has an "Full call I/O (sidecar)" expander with tabs for user prompt, response, system prompt.
- [x] Per-epoch / per-agent filtering in the sidebar.
- [x] Persona editor (`ui/personas_view.py`): file picker + text area + Save/Reset/Reload. Edits land on disk and take effect on next `run.py` invocation.
- [ ] Atomic sidecar writes (write-to-tmp-then-rename) so a crash mid-write does not leave a partial file — still a real concern under parallelism since multiple workers write sidecars concurrently.
- [ ] Markdown transcript download (`.md` via `EventLog.to_markdown()`) from the transcript page sidebar.

### 8. Variants generator + export bundle

- [x] Finalization pipeline (`Moderator.run_phase_4`): `Psychometrician.audit_exam` → `Accessibility.generate_variant` for each required `AccommodationKind` × surviving item (parallelized) → Quarto export. Variants accept "no change" answers and keep them — provenance-defensible.
- [x] Export bundle in `export/` (Quarto-based):
  - [x] `exam.qmd` → PDF + DOCX + LaTeX
  - [x] `answer_key.qmd` → PDF + DOCX + LaTeX
  - [x] `rubrics.qmd` → PDF + DOCX + LaTeX (only emitted when items have rubrics)
  - [x] `exam_report.qmd` → PDF + DOCX (Bloom distribution, difficulty curve, CLO coverage, imbalance notes, exam-level findings)
  - [x] `variants/<item_id>_<kind>.qmd` → PDF + DOCX + LaTeX, one per (item, kind) pair
  - [x] `provenance.json` (full audit trail: draft + audit + variants)
  - [x] `render_failures.json` (only written when a Quarto format fails — per-format failures don't abort the bundle)
  - [ ] `exam.qti.zip` (Canvas/Blackboard) — deferred; consider via the `canvas_cartridge` skill
- [ ] Streamlit Final Review screen (Checkpoint 3: full preview, variants tab, coverage heatmaps, exam-level report)
- [ ] Streamlit Export screen

### 9. Provenance UI / grade-appeal export view

- [ ] Streamlit per-item provenance viewer (which agent proposed, which objections, how each resolved)
- [ ] Grade-appeal export: filter provenance to one item, render as a defensible record

---

## Cross-cutting infrastructure (not in §10 but needed)

- [ ] `requirements.txt` for ai_exam (current deps: `anthropic >= 0.40`, `pydantic >= 2`). Decide whether to keep at parent repo or add per-project.
- [ ] `.env_sample` (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL_OPUS`, `ANTHROPIC_MODEL_SONNET`, vector store config)
- [ ] `run.sh` / `run_in_background.sh` matching sibling-app conventions, on a port not used by other tools (check `start_all_ai_apps.sh` at repo root)
- [ ] Logging: structured JSON to a per-session file (sibling app pattern; useful for replaying agent calls)
- [ ] Test suite: at minimum, agent verb contracts mocked against a fake Anthropic client that returns canned tool_use blocks. End-to-end with real API gated behind an env flag.
- [ ] `mypy --strict` config + CI hook

---

## Open design questions

- [ ] **Retrieval call site for SME.** Today's verbs accept pre-retrieved chunks; SME also has `gather_context` for follow-up retrieval. Decide before wiring the orchestrator: does the orchestrator always pre-retrieve, or does the SME issue its own queries inside `propose_items` when its initial chunk set is sparse?
- [ ] **Rule-based tools vs. persona discipline.** Design doc §3 lists item-flaw checklists, readability scorers, jargon detectors, Bloom verb classifiers as agent tools. Current scaffold encodes the rules in personas. Build any as actual callables? Cost: complexity + maintenance. Benefit: deterministic catch of known patterns even when the LLM misses them.
- [ ] **Model assignment per agent.** Design §8: Opus for SME and Adversarial Student, Sonnet for everything else. Currently each agent takes `model` as a constructor arg — decide on a registry / factory pattern in the orchestrator so the assignment is in one place, not scattered across callers.
- [ ] **Phase-2 batching vs. per-cell.** Design says SME proposes 1.5× target per cell. With ~50 cells per exam, that is ~50 sequential SME calls. Worth parallelizing across cells (cheap with concurrent SDK calls)? Locks in the per-cell verb shape we already have.
- [ ] **Exam-state size for `audit_exam`.** `ExamDraft` serialized with 50+ items + blueprint + objections will be large. May need to summarize or batch for the Psychometrician. Defer until first run shows the actual size.
- [ ] **Persona "shared appendix".** Each critic persona repeats the severity rubric and the "stay in your lane" framing. Worth hoisting into a shared appendix the BaseAgent loads alongside the per-agent persona? Or accept the duplication for clarity?
- [ ] **Failure modes for `_invoke`.** Currently raises `AgentResponseError` if Claude returns no `tool_use` block, and `ValidationError` if the input fails Pydantic. No retry logic. Decide if the orchestrator handles retries or if BaseAgent does.

---

## Lessons from real-data debug runs (pchem_notes.pdf)

Two runs, three bugs fixed during debug, two real patterns observed.

### Bugs found + fixed
| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `BadRequestError: 'temperature' is deprecated for this model` | Opus 4.7 rejects `temperature` param | Removed from `BaseAgent._invoke` |
| 2 | `ValidationError: themes — Input should be a valid list, input_type=str` | Opus occasionally stringifies the entire response under one schema key (`{"themes": "<JSON-of-whole-response>"}`) | Two-part: clearer tool description telling the model not to stringify nested values; defensive unwrap fallback in `_validate_tool_input` that recovers the embedded JSON |
| 3 | `topic_units` cell over-accepted 1 item even though `target_item_count=0` | Per-cell loop checked break condition *after* appending the item | Moved break check to top of loop; also added explicit skip-cell short-circuit when `target_item_count <= 0` |
| 4 | `ValidationError: EditResult.updated_draft — Input should be a valid dictionary or instance of ItemDraft, input_type=str` | Opus stringifies SOME nested fields while leaving siblings as plain JSON (`{"updated_draft": "<JSON-of-ItemDraft>", "rationale": "ok"}`) — generalization of bug #2 | Extended `_validate_tool_input` to try `json.loads` on every string value; if a value parses to dict/list, swap and re-validate. Genuine string fields are untouched because primitive parse results are kept as-is. All four unwrap strategies are unit-tested. |

### Patterns observed (not bugs, but design pressure)

- **SME inflates Bloom level.** When a blueprint cell asks for ANALYZE, SME tends to write APPLY-level work and label it ANALYZE. LOA catches it accurately — multiple-paragraph diagnoses pointing at the specific cognitive operations. Items are getting rejected when LOA's `suggest_realignment` could often save them with a relabel. Suggests adding a `suggest_realignment` fallback in Phase 2 routing.
- **Grounding Verifier required a precise persona to be useful.** First persona was too strict: rejected any item whose stem provided numerical inputs (because those inputs weren't in the chunks). Second persona explicitly distinguishes "knowledge claims" (formulas, rules, definitions — must be in chunks) from "stated inputs" (numerical values supplied by the stem — fair game). This single persona change moved one run from 7 accepted / 6 rejected (26 pts) to 8 accepted / 4 rejected (32 pts), with the remaining rejections being defensible.
- **The multi-agent design is paying off.** Rejection rationales are pedagogically substantive — the kind of feedback a careful colleague would give. The system is functioning as a collaborative reviewer, not a generator + rubber-stamp.

### Phase 3 first real-data run (1 epoch on the pchem corpus)

Entering Phase 3 with 7 items (25 pts). Single epoch produced:

| Severity | New objections raised | |
|---|---:|---|
| critical | 1 | |
| high | 8 | |
| medium | 23 | |
| low | 17 | |
| **total** | **49** | across 7 items × 3 critics |

| SME stance | Count | |
|---|---:|---|
| ACCEPT (then edit + re-verify) | 11 | |
| REBUT | 34 | |
| DEFER | 0 | |

| Outcome | Count | |
|---|---:|---|
| Objections resolved (edit + re-verify passed) | 11 | |
| Items rejected (post-edit re-verify failed) | 1 | LOA caught a SME edit that drifted the item into APPLY from a cell that demanded APPLY but was labeled differently |
| Items surviving | 6 (22 pts) | status=REFINED |
| Open at end | 38 (4 critical+high) | did not converge — capped at max_epochs=1 |

Phase-3 Claude calls: 113 total (45 SME rebut + 12 SME edit + 12 IWS cleanup + 12 LOA verify + 11 Grounding verify + 7 Accessibility + 7 Adversarial + 7 Psychometrician). Runtime ≈ 25 min.

**Observations from Phase 3:**
- **High SME rebut rate (69%, 34/49)** — the anti-sycophancy framing in the persona is taking hold. Hard to tell yet whether this is the right rate or whether SME is over-defending. Needs more epochs and more corpora.
- **Zero defers.** SME is decisive. The persona's "empty agreement is worse than a clean disagreement" framing reads as "pick a side."
- **Post-edit re-verification is necessary.** One out of 12 SME edits broke alignment — without the IWS-LOA-Grounding re-check, that item would have shipped silently broken.
- **Non-convergence at 1 epoch is expected.** 38 open objections (4 critical+high) remain. Most are MEDIUM/LOW which the convergence rule ignores; CRITICAL+HIGH are the blockers. Needs ~2-3 more epochs to converge on this corpus.

### Inputs that landed for the test corpus
- `test_data/course_spec.json` — 7 CLOs (apply + analyze mix), 7 weighted topics, principles
- `test_data/exam_spec.json` — quiz, 30 pts, 40 min, 4 MCQ + 2 short + 2 problem
- `test_data/policy.json` — content_fidelity > cognitive_alignment > accessibility > discrimination > brevity, max_epochs=4
- `test_data/pchem_notes.pdf` (4 pages → 7 Chroma chunks)

---

## Done in this scaffold

- 7 LLM agent classes with typed verbs, each loading its persona constitution from `persona/<name>.md`
- Pydantic data model layer (29 models / enums covering Phase 0 through Phase 4)
- `BaseAgent` with system-prompt caching (`cache_control: ephemeral`) and forced-tool-use structured output (Pydantic schema → JSON schema → tool definition → validated response)
- `Retriever` Protocol + `FakeRetriever` for tests and local wiring
- Adversarial Student answer-leaking field redaction (`_student_view`) with whitelisted visible fields and unit-verified non-leakage
- Two-tier draft/promoted models (`ItemDraft`/`Item`, `ObjectionDraft`/`Objection`) so critics and proposers cannot fabricate Moderator-owned fields (ids, status, agent stamps, provenance)
- **Event log infrastructure**: every BaseAgent `_invoke` call automatically emits `invocation_started` and `invocation_completed` (or `invocation_failed`) events to an append-only JSONL log with filelock-safe concurrent writes. Verb name captured via `inspect.stack()` so subclasses do not pass it explicitly. Full system prompt + user prompt + raw response stored in `calls/<call_id>.json` sidecars; events themselves carry only summaries. Failure path records the failure event but propagates the original exception with original type and traceback (no wrapping). Markdown transcript formatter produces a chat-style rendering ready for download.
