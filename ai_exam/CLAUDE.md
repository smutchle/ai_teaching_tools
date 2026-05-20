# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This directory is currently **design-only** — no code, build, test, or run commands exist yet. The contents are:

- `exam_agent_design.md` — technical design (data models, agent roster, orchestration, UI surfaces, build order)
- `exam_agent_proposal.md` (+ `.pdf`) — narrative proposal for departmental review
- `Exam Design Intake — College of Science.pdf` — intake form / requirements source

Treat the design doc as the authoritative spec when implementation begins. The proposal explains *why*; the design doc specifies *what*. When asked to start building, follow the **Build Order** in §10 of `exam_agent_design.md` (data models + SQLite → ingestion + retrieval → SME/Blueprint through checkpoint 1 → item-gen loop → critics one at a time → moderator → live view → variants/export → provenance UI).

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

- **Conda env**: `genai` (Python 3.10). Activate before running anything.
- **UI**: Streamlit, with `run.sh` (foreground) and `run_in_background.sh` (nohup) scripts at the app root. Pick a port not already used by sibling apps (check `start_all_ai_apps.sh` at the repo root).
- **Config**: `.env` file (gitignored), with a `.env_sample` committed. Anthropic and/or VT ARC (OpenAI-compatible) are the standard LLM providers used elsewhere in this repo.
- **Persistence**: sibling apps use JSON-on-disk with `filelock`; this project's design calls for **SQLite** (one row per item version, objection, and provenance event — resumable across sessions). Use SQLite here, not JSON.
