# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

**Phase 0–3 are live and verified end-to-end against `test_data/pchem_notes.pdf`.** Phase 4 (variants + exam audit + export bundle) and the Streamlit UI are not yet built. See `TODO.md` for the full status, the handoff section, and the ordered list of next-most-valuable work.

The original design docs remain authoritative for *what* and *why*:
- `exam_agent_design.md` — technical design (data models, agent roster, orchestration, UI surfaces, build order)
- `exam_agent_proposal.md` (+ `.pdf`) — narrative proposal for departmental review
- `Exam Design Intake — College of Science.pdf` — intake form / requirements source

Run the pipeline:
```bash
conda run --no-capture-output -n genai python run.py --pdf test_data/pchem_notes.pdf --max-epochs 1
# Outputs land under runs/run_<timestamp>/
```

Prerequisites: `.env` with `ANTHROPIC_API_KEY`; Ollama running at `localhost:11434` with `nomic-embed-text-v2-moe:latest`.

## Code layout

```
ai_exam/
├── run.py                        CLI entry point
├── config.py                     env + Anthropic client + per-agent model registry
├── events.py                     EventLog (JSONL + sidecar full-I/O + Markdown formatter)
├── models.py                     all Pydantic contracts between agents (~30 types)
├── agents/                       one BaseAgent subclass per role; each loads persona/<name>.md
├── persona/                      .md system prompt per agent ("constitution")
├── moderator/                    deterministic orchestrator (Phase 0-3 state machine + TradeOffPolicy)
├── retrieval/                    Protocol + FakeRetriever + ChromaRetriever + OllamaEmbedder + pypdf ingestion
├── test_data/                    pchem_notes.pdf + course_spec/exam_spec/policy JSON
└── runs/                         per-run outputs (gitignored; created at runtime)
```

## Load-bearing code (do not remove without thought)

Real bugs surfaced during debug; the fixes are non-obvious and the original failure modes will resurface if removed:

- **`_validate_tool_input` in `agents/base.py`** (four-strategy unwrap). Opus 4.7 sometimes stringifies (a) the whole response, (b) the response under a single-key wrapper, or (c) individual nested object/array fields while sibling fields are correct. The unwrap recovers all three patterns. Without it, Phase 3 breaks within the first SME edit.
- **No `temperature` parameter in `BaseAgent._invoke`.** Opus 4.7 rejects `temperature` as deprecated (`BadRequestError 400`). Don't reintroduce it.
- **Top-of-loop break check + zero-target short-circuit in `Moderator._phase_2_cell`.** Guards against off-by-one over-acceptance when the Blueprint Architect produces a `target_item_count=0` cell (it does — when it folds a topic's points into other cells via cross-citations).

## Architectural commitments (from the design doc)

These are load-bearing — changing them means re-litigating the proposal:

- **Multi-agent, not single-agent.** Each critic agent (LOA, Item-Writing Specialist, Accessibility, Psychometrician, Adversarial Student) is separate by design. The point is to make trade-offs visible, not to optimize them away. Empty critique from a critic must be rejected and re-prompted (anti-sycophancy rule, §3).
- **Moderator is deterministic, not an LLM.** It applies the professor's trade-off policy as a priority-ranked tiebreaker. Resist any temptation to make it an agent.
- **Three human-in-the-loop checkpoints gate progression**: blueprint approval → item bank review → final draft review. Agents cannot move past a checkpoint without instructor sign-off.
- **Grounding is mandatory.** SME item proposals require ≥1 `source_ref` to a chunk from uploaded materials, and a separate Grounding Verifier (lightweight LLM call) checks the cited chunks actually support the answer. Items without grounding are auto-rejected. Prior exams are indexed for *style only* — never as a source for new items.
- **Full provenance.** Every proposal/edit/objection/resolution writes a `ProvenanceEvent`. This is what makes exams defensible against grade appeals; do not short-circuit it for convenience.
- **No agent-orchestration frameworks.** Despite the design doc's LangGraph recommendation, all agents and the orchestration loop must be coded from scratch (direct Anthropic SDK calls, plain Python state machine, explicit checkpointing). Do not use CrewAI, AutoGen, LangGraph, or LangChain *agent* abstractions. Frameworks for non-agent concerns are fine and encouraged where they pull weight — RAG, retrievers, vector stores, document loaders, chunkers, GraphRAG, embedding pipelines, tool implementations (e.g., LangChain retrievers, LlamaIndex, Chroma/LanceDB, Microsoft GraphRAG). The rule is: scratch-built agents + orchestration; libraries fine for everything else. Models: Claude Opus for SME and Adversarial Student, Claude Sonnet for everything else, configurable per-agent.

## Code conventions

- **Strong typing throughout.** Type-annotate every function signature and dataclass/Pydantic field. Run `mypy --strict` (or equivalent) clean. No bare `Any`, no untyped `dict`/`list` returns from public functions.
- **Never bury or wrap exceptions.** No bare `except:`, no `except Exception: pass`, no catching only to log-and-re-raise-as-RuntimeError. If you must catch, catch the specific type, handle it meaningfully, and let everything else propagate with its original traceback intact. Wrapping destroys the stack and the original error type — don't do it.
- **UI: Streamlit by default.** Use Streamlit unless a specific requirement (real-time bidirectional updates, complex custom widgets, multi-user concurrent editing beyond what `filelock` + polling handles) genuinely can't be met. If you think you need something else, raise it with the user before switching.

## Conventions inherited from sibling tools

This directory sits inside `ai_teaching_tools/`, which uses consistent conventions across apps. Mirror them when scaffolding:

- **Conda env**: `genai` (Python 3.10). Already has every dep this project uses: `anthropic`, `pydantic` (v2), `chromadb`, `sentence_transformers`, `pypdf`, `filelock`, `httpx`, `python-dotenv`, `ollama` (Python client). Activate before running anything.
- **UI**: Streamlit, with `run.sh` (foreground) and `run_in_background.sh` (nohup) scripts at the app root. Pick a port not already used by sibling apps (check `start_all_ai_apps.sh` at the repo root). **Not yet built for this project** — ask the user before starting any UI work; they've explicitly said to be told first.
- **Config**: `.env` file (gitignored), with a `.env_sample` committed. Anthropic and/or VT ARC (OpenAI-compatible) are the standard LLM providers used elsewhere in this repo. This project uses Anthropic for agents and a local Ollama server for embeddings.
- **Persistence**: sibling apps use JSON-on-disk with `filelock`; this project's *design* calls for SQLite (one row per item version, objection, and provenance event — resumable across sessions). Today we use per-phase JSON snapshots + the JSONL event log; SQLite migration is open work — see `TODO.md` §1.

## Where to look for things

- "How does Phase X work?" → `moderator/moderator.py` — Phase 0-2 in `run_through_checkpoint_2`, Phase 3 in `run_through_checkpoint_3` + `_run_epoch` + `_sme_edit_and_reverify`.
- "What does agent X say in its system prompt?" → `persona/<persona_name>.md`. The agent class's `PERSONA_NAME` class var matches the file stem.
- "What's a `ProvenanceEvent`?" → `models.py`. All Pydantic contracts between agents live there; ordered top-to-bottom by dependency (enums, then leaf types, then composed types).
- "What was logged during run X?" → `runs/run_<ts>/events/events.jsonl` (summary timeline), `runs/run_<ts>/events/calls/<call_id>.json` (full system prompt + user prompt + raw response per call), `runs/run_<ts>/transcript.md` (chat-style rendering of the events).
- "What's done and what's next?" → `TODO.md` — handoff section at the top is the fastest orientation.
