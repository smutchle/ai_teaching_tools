# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

**Phase 0–4 are live and verified end-to-end against `test_data/pchem_notes.pdf`.** The Streamlit UI is built (5 pages: Run / Job Monitor / Job Outputs / Personas / Documentation). Per-run narrative generation is wired (post-hoc Haiku polish of an event-stream template, rendered to PDF + HTML). See `TODO.md` for the full status, the handoff section, and the ordered list of remaining open work.

The original design docs remain authoritative for *what* and *why*:
- `specifications/exam_agent_design.md` — technical design (data models, agent roster, orchestration, UI surfaces, build order)
- `specifications/exam_agent_proposal.md` (+ `.pdf`) — narrative proposal for departmental review
- `specifications/Exam Design Intake — College of Science.pdf` — intake form / requirements source

## How to run

Two entry points:

```bash
# 1. UI (recommended). Defaults to Sonnet HIGH + Haiku LOW, full pipeline (no skip toggles).
./run.sh                  # foreground, port 8550
./run_in_background.sh    # nohup

# 2. CLI direct.
conda run --no-capture-output -n genai python run.py \
  --pdf test_data/pchem_notes.pdf \
  --high-provider anthropic --high-model claude-sonnet-4-6 \
  --low-provider anthropic --low-model claude-haiku-4-5-20251001
# Outputs land under runs/run_<timestamp>/
```

**Prerequisites**:
- `genai` conda env (already has every dep — `anthropic`, `openai`, `pydantic`, `chromadb`, `pypdf`, `httpx`, `filelock`, `streamlit`, `plotly`)
- `.env` with at minimum the API keys for providers you actually route to (`ANTHROPIC_API_KEY`, `ARC_API_KEY`, `OLLAMA_HOST`/`OLLAMA_MODEL`). The default routing uses Anthropic — that's the only key strictly required.
- Ollama running locally at `localhost:11434` with `nomic-embed-text-v2-moe:latest` (always used for chunk embeddings, regardless of which LLM provider you pick for agents).
- `quarto` on PATH (rendered exam bundle + narrative).

## Code layout

```
ai_exam/
├── run.py                        CLI entry point
├── config.py                     env + provider clients + PERSONA_TIER + MODEL_REGISTRY
├── providers.py                  AnthropicProvider + OpenAIProvider (covers ARC, Ollama, vLLM)
├── parallel.py                   gather_sync — threadpool fan-out over agent calls
├── events.py                     EventLog (JSONL + sidecar full-I/O + Markdown formatter)
├── models.py                     all Pydantic contracts between agents (~30 types)
├── agents/                       one BaseAgent subclass per role; each loads persona/<name>.md
├── persona/                      .md system prompt per agent ("constitution")
├── moderator/                    deterministic orchestrator (Phase 0-4 state machine + TradeOffPolicy)
├── retrieval/                    Protocol + FakeRetriever + ChromaRetriever + OllamaEmbedder + pypdf ingestion
├── export/                       Phase 4 Quarto bundle builder (exam, answer key, instructor notes, rubrics, report, variants)
├── narrative/                    post-hoc run narrator: templater + NarratorAgent + Quarto render
├── ui/                           Streamlit app (5 pages) + diagrams/
├── test_data/                    pchem_notes.pdf + course_spec/exam_spec/policy JSON
├── uploads/                      per-launch staged PDFs + spec JSONs (created by UI launches)
└── runs/                         per-run outputs (gitignored; created at runtime)
```

## Load-bearing code (do not remove without thought)

Real bugs surfaced during debug; the fixes are non-obvious and the original failure modes will resurface if removed:

### Model-output robustness (agents/base.py)
- **`_recursive_unwrap` + `_repair_json_escapes`** in `_validate_tool_input`. Models stringify nested fields (Opus, Sonnet) and emit invalid JSON escapes like `\Delta` inside stringified strings (Sonnet). The recursive unwrap descends into stringified dicts/lists at any depth; the escape repair doubles `\X` when X isn't a valid JSON escape character. Without these, the pipeline crashes within the first complex schema response.
- **Retry-on-validation loop in `_invoke`** (`max_attempts=2` default). When validation fails or the model omits a required field, the user prompt is augmented with the Pydantic error and the call retries. Catches ~99% of model-output quirks without per-field whack-a-mole. Each attempt writes its own sidecar (`<call_id>.json`, `<call_id>_attempt_2.json`).
- **No `temperature` parameter passed to providers.** Opus 4.7 rejects `temperature` as deprecated (`BadRequestError 400`).

### Defensive defaults (models.py)
- **`ItemDraftList.items`, `ObjectionDraftList.objections`, `ThemeList.themes`, `RebuttalBatch.rebuttals`, `ItemObjectionsBatch.items`, `ExamAudit.objections`** all default to `[]`. A model returning `{}` means "produced nothing," not a crash.
- **`EditResult.rationale` and `Rebuttal.rationale`** default to `""`. Haiku is terse and frequently omits explanatory fields.

### Per-cell + per-item safe wrappers (moderator/moderator.py)
- **`_safe_phase_2_cell`** catches `ValidationError` and `AgentResponseError` from a single cell and converts to an empty `Phase2Outcome`. One bad cell never collapses the gather.
- **`_safe_route_item_objections`** does the same for Phase 3 routing — one bad item gets marked rejected with a routing event, the rest of the epoch continues.
- **`_try_realignment`** in `_phase_2_cell` — when LOA rejects for Bloom mismatch, calls `LOA.suggest_realignment`, auto-applies `remap_bloom` or `remap_clos`, re-verifies. Recovers ~65% of historical Phase-2 rejections.

### Concurrency control (config.py + providers.py)
- **Per-provider `threading.Semaphore`**: Ollama=1 (single GPU serializes), ARC=4 (free quota is 30/hr), Anthropic=8. Without the Ollama cap, parallel fan-out times out.
- **`max_tokens_floor=16384` for Ollama**. Thinking models (gemma4:31b, gpt-oss) emit thousands of reasoning tokens before producing JSON content. Default 4k caps the whole completion — reasoning eats it, content comes back empty.
- **`self._item_counter_lock` in `Moderator`** — Phase 2 cells run in parallel and the item-id counter is the only cross-worker shared state.
- **`_route_item_objections` is per-item by design** — never collapse it back into a flat `for objection in objections_open` loop. The per-item grouping is what makes the parallel fan-out safe (same-item edits stay sequential).

### Quarto rendering (export/templates.py)
- **`_normalize_markdown`** applied to every SME-authored text field (stems, answers, rubrics) before rendering: strips inline-bold from list markers (`**(a)**` → `(a)`), canonicalizes letter markers to uppercase `A.` form, inserts blank lines before list items.
- **`_strip_option_letter_prefix`** on MCQ options — SME sometimes embeds the letter prefix into the option text (`"A) The units are…"`), which would double up with the renderer's letter (`"A. A) …"`).
- **`from: markdown+fancy_lists` in the QMD frontmatter** — required for Pandoc to parse `(a)` / `A.` markers as ordered lists.
- **TWO spaces after capital-letter MCQ markers** (`"A.  Option"` not `"A. Option"`). Pandoc fancy_lists requires the double space to distinguish list markers from initials.

### UI (ui/run_launcher.py)
- **`is_pid_alive` reads `/proc/<pid>/status`** and rejects `State: Z` (zombie). Plain `os.kill(pid, 0)` returns success on zombies → UI reports crashed pipelines as still running.

### Phase 0 safety (moderator/moderator.py)
- **Top-of-loop break check + zero-target short-circuit in `_phase_2_cell`** — guards against the off-by-one when BA produces `target_item_count=0` cells (BA does this when folding a topic's points into cross-citations on other cells).

## Architectural commitments

- **Multi-agent, not single-agent.** Each critic (LOA, IWS, Accessibility, Psychometrician, Adversarial Student) is a separate agent. The point is to make trade-offs visible, not to optimize them away.
- **Moderator is deterministic, not an LLM.** It applies the trade-off policy as a priority-ranked tiebreaker. Don't make it an agent.
- **Grounding is mandatory.** SME item proposals require ≥1 `source_ref` to a chunk from uploaded materials; the Grounding Verifier checks the cited chunks actually support the answer. Items without grounding are auto-rejected. Prior exams are indexed for *style only* — never as a source for new items.
- **Full provenance.** Every proposal / edit / objection / resolution writes a `ProvenanceEvent`. This is what makes exams defensible against grade appeals; do not short-circuit it for convenience.
- **No agent-orchestration frameworks.** Despite the design doc's LangGraph recommendation, all agents and orchestration logic are coded from scratch (direct provider SDK calls, plain Python state machine, explicit checkpointing). Do NOT use CrewAI, AutoGen, LangGraph, or LangChain *agent* abstractions. Frameworks for non-agent concerns are fine — RAG / retrievers / vector stores / GraphRAG / Chroma / LanceDB are all OK.
- **Two-tier model routing** (HIGH / LOW) via `config.PERSONA_TIER` + `MODEL_REGISTRY`. HIGH: SME, Blueprint Architect, Adversarial Student. LOW: IWS, LOA, Grounding Verifier, Accessibility, Psychometrician, Narrator. Defaults: Sonnet HIGH + Haiku LOW.
- **Observation-only UI, auto-approved checkpoints.** The design doc calls for human-in-the-loop at CP1/CP2/CP3; we explicitly chose kick-off-and-watch UX instead. CP1 and CP2 auto-approve. CP3 is the end-of-pipeline marker, not a gate. Faculty review happens against the rendered bundle, not mid-run.

## Code conventions

- **Strong typing throughout.** Type-annotate every function signature and dataclass/Pydantic field. No bare `Any`, no untyped `dict`/`list` returns from public functions.
- **Never bury or wrap exceptions.** No bare `except:`, no `except Exception: pass`, no catching only to log-and-re-raise-as-RuntimeError. If you must catch, catch the specific type, handle it meaningfully, and let everything else propagate with its original traceback intact.
- **UI: Streamlit by default.** The current 5-page UI is the convention.

## Conventions inherited from sibling tools

This directory sits inside `ai_teaching_tools/`. Sibling conventions:

- **Conda env**: `genai` (Python 3.10).
- **UI launchers**: `run.sh` (foreground) and `run_in_background.sh` (nohup) at the app root. This project uses **port 8550**.
- **Config**: `.env` (gitignored). Three provider blocks: Anthropic, ARC (VT's OpenAI-compatible endpoint, free with quota), Ollama (local). Choose any combination per agent via `config.MODEL_REGISTRY`.
- **Persistence**: per-phase JSON snapshots + JSONL event log + per-call sidecars. SQLite migration is open work (see `TODO.md`).

## Where to look for things

- "How does Phase X work?" → `moderator/moderator.py`. Phase 0–2 in `run_through_checkpoint_2`, Phase 3 in `run_through_checkpoint_3` + `_run_epoch` + `_sme_edit_and_reverify`, Phase 4 in `run_phase_4`.
- "What does agent X say in its system prompt?" → `persona/<persona_name>.md`. The agent class's `PERSONA_NAME` class var matches the file stem.
- "What's a `ProvenanceEvent`?" → `models.py`. All Pydantic contracts between agents live there.
- "What was logged during run X?" → `runs/run_<ts>/events/events.jsonl` (summary timeline), `runs/run_<ts>/events/calls/<call_id>.json` (full system/user prompt + raw response). The Job Monitor page surfaces the same data as an interactive Plotly timeline with click-to-drill.
- "How is the exam bundle built?" → `export/templates.py` (QMD source) + `export/render.py` (Quarto subprocess) + `export/bundle.py` (orchestration).
- "How is the run narrative generated?" → `narrative/templater.py` (events → structured markdown) + `agents/narrator.py` (Haiku polish) + `narrative/builder.py` (Quarto render to PDF + HTML).
- "What's done and what's next?" → `TODO.md` — handoff section at the top is the fastest orientation.
