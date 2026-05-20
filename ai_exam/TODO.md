# TODO

Status tracker for the agentic exam-generator build. Organized against the **Build Order** in `exam_agent_design.md` §10 — items map back to the original plan so you can see what is on-spec, what has slipped, and what is still ahead.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

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
| Moderator Phase 3 (parallel critique + epoch loop + routing) | in progress |
| `config.py` + `.env` + per-agent model registry | done |
| `run.py` CLI entry point | done |
| SQLite persistence layer | not started |
| Streamlit UI (any screen) | not started |
| Streamlit chat transcript view + download buttons | not started |
| Export bundle (DOCX/PDF/QTI/LaTeX/JSON) | not started |

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

- [x] `Moderator` deterministic router (not an LLM): Phase 0–2 state machine in place
- [x] `TradeOffPolicy` data type (`moderator/policy.py`): priority ranking over 5 dimensions + max_epochs + convergence rule. Phase 0–2 loads it but does not consult it; Phase 3 will use it for conflict resolution.
- [x] Promotion logic: `ItemDraft` → `Item` (assign id, status=draft, append SME-proposed + IWS-edited provenance); `ObjectionDraft` → `Objection` model exists; Phase 3 will do the stamping
- [x] Provenance append on every state change in Phase 0–2 (SME proposed, IWS edited, LOA accepted/rejected, Grounding accepted/rejected)
- [ ] Phase 3 epoch loop: parallel (or sequential for v0) critique by Accessibility + Adversarial Student + Psychometrician; LOA + IWS re-check after edits
- [ ] Phase 3 routing: critical → force SME edit or reject; conflict → priority_rank tiebreaker; otherwise → SME accept/rebut/defer
- [ ] Anti-sycophancy enforcement: re-prompt critics whose epoch-1 critique is empty (per design §3)
- [ ] Rebut-reassert loop bounded to 2 rounds before professor escalation (per design §5)
- [ ] Convergence detection: exit when no critical/high objections open at end of an epoch
- [ ] True parallelism for Phase 3 critique pass (sync first for debug; switch to async once the loop is stable)

### 7. Refinement live view UI

- [x] `events.py` infrastructure (AgentEvent, EventLog, EventKind, JSONL + sidecar, Markdown formatter, filelock-safe concurrent appends)
- [x] BaseAgent emits invocation_started / invocation_completed / invocation_failed automatically on every `_invoke`; verb name captured from call stack; tokens + duration recorded; failure path preserves original exception
- [ ] Moderator emits routing_decision / policy_applied / checkpoint_reached / provenance_appended into the same log (depends on Moderator being built)
- [ ] Streamlit Refinement Live View — read EventLog, render each event as `st.chat_message(agent_name)`, poll via `@st.fragment(run_every=1.0)` or `time.sleep(1); st.rerun()`
- [ ] Streamlit sidebar download buttons: `.jsonl` (raw events file) and `.md` (rendered transcript via `EventLog.to_markdown()`)
- [ ] Event-detail drawer: clicking a chat bubble loads the sidecar `calls/<call_id>.json` and shows full system prompt, user prompt, and raw response
- [ ] Per-epoch / per-agent filtering in the UI
- [ ] Atomic sidecar writes (write-to-tmp-then-rename) so a crash mid-write does not leave a partial file

### 8. Variants generator + export bundle

- [ ] Finalization pipeline: Accessibility.generate_variant for each required `AccommodationKind`; Psychometrician.audit_exam for the exam report
- [ ] Export bundle per design §9:
  - [ ] `exam.docx` (primary, faculty-editable)
  - [ ] `exam_variants/*.docx`
  - [ ] `answer_key.docx`
  - [ ] `rubrics.docx`
  - [ ] `exam.qti.zip` (Canvas/Blackboard)
  - [ ] `exam_report.pdf` (blueprint, coverage, Bloom distribution, est. difficulty curve)
  - [ ] `provenance.json` (full audit trail)
  - [ ] `exam.tex` (optional)
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

### Patterns observed (not bugs, but design pressure)

- **SME inflates Bloom level.** When a blueprint cell asks for ANALYZE, SME tends to write APPLY-level work and label it ANALYZE. LOA catches it accurately — multiple-paragraph diagnoses pointing at the specific cognitive operations. Items are getting rejected when LOA's `suggest_realignment` could often save them with a relabel. Suggests adding a `suggest_realignment` fallback in Phase 2 routing.
- **Grounding Verifier required a precise persona to be useful.** First persona was too strict: rejected any item whose stem provided numerical inputs (because those inputs weren't in the chunks). Second persona explicitly distinguishes "knowledge claims" (formulas, rules, definitions — must be in chunks) from "stated inputs" (numerical values supplied by the stem — fair game). This single persona change moved one run from 7 accepted / 6 rejected (26 pts) to 8 accepted / 4 rejected (32 pts), with the remaining rejections being defensible (e.g., asking about ΔG° of a *reversed* reaction when chunks state sign-flip only for ΔH°).
- **The multi-agent design is paying off.** Six items got dropped across the two runs. Each rejection rationale is pedagogically substantive — the kind of feedback a careful colleague would give. The system is functioning as a collaborative reviewer, not a generator + rubber-stamp.

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
