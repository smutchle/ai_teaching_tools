# Agentic Exam Generator — Design Document

A multi-agent system for generating BS/MS science exams. Streamlit frontend, agent orchestration backend, faculty-in-the-loop at defined checkpoints.

---

## 1. Inputs (Professor Provides)

**Materials**
- Course readings, lecture notes, problem sets, lab manuals (PDF, DOCX, MD, plain text)
- Optional: prior exams (used as style/difficulty reference, never as item source)

**Course Specification**
- Course learning outcomes (CLOs), one per line, each tagged with target Bloom level
- Topic list with relative weights (sums to 1.0)
- Guiding principles (free text: e.g., "emphasize quantitative reasoning," "no rote definitions")

**Exam Specification**
- Exam type: midterm | final | quiz | qualifying
- Total points and time budget (minutes)
- Item-type counts: `{mcq: int, short_answer: int, problem: int, derivation: int, data_interp: int}`
- Difficulty distribution: target % easy/medium/hard
- Accommodations required: extended-time variant, screen-reader variant, large-print variant
- Equation rendering: LaTeX required y/n; figure/data-table support y/n

**Trade-off Policy** (resolves agent disagreement)
- Priority ranking over: content_fidelity, cognitive_alignment, accessibility, discrimination, brevity
- Max epochs (default 4)
- Convergence rule: no Critical or High objections raised for one full epoch

---

## 2. Core Data Models

```python
# Pydantic models — these are the contracts between agents

class CourseSpec:
    clos: list[CLO]                    # CLO: id, text, bloom_level, knowledge_type
    topics: list[Topic]                # Topic: id, name, weight, source_refs
    guiding_principles: str

class Blueprint:
    cells: list[BlueprintCell]         # cell: (topic_id, bloom_level) -> target_item_count, target_points
    coverage_check: dict               # validation against CLOs

class Item:
    id: str
    type: ItemType                     # mcq | short_answer | problem | derivation | data_interp
    stem: str                          # LaTeX-aware
    options: list[str] | None          # mcq only
    answer_key: str
    rubric: str | None                 # non-mcq
    points: int
    bloom_level: BloomLevel
    knowledge_type: KnowledgeType
    clo_refs: list[str]                # which CLOs this item is evidence for
    topic_refs: list[str]
    source_refs: list[SourceRef]       # which uploaded material this came from
    difficulty_est: Literal["easy", "medium", "hard"]
    discrimination_est: float | None   # 0-1, agent-estimated
    accessibility_notes: list[str]
    provenance: list[ProvenanceEvent]  # full audit trail
    status: Literal["draft", "refined", "accepted", "rejected"]

class Objection:
    agent: str
    severity: Literal["critical", "high", "medium", "low"]
    category: str                      # e.g., "construct_irrelevant_difficulty"
    target: str                        # item_id or "blueprint" or "exam_global"
    claim: str
    suggested_fix: str | None

class ProvenanceEvent:
    epoch: int
    agent: str
    action: Literal["proposed", "edited", "objected", "accepted", "resolved"]
    diff: str | None
    rationale: str
    timestamp: datetime

class ExamDraft:
    items: list[Item]
    blueprint: Blueprint
    objections_open: list[Objection]
    objections_resolved: list[Objection]
    epoch: int
```

---

## 3. Agent Roster

Each agent: system prompt, allowed actions, output schema (forced JSON), tools.

| Agent | Role | Allowed Actions | Key Tools |
|---|---|---|---|
| **SME** (Subject Matter Expert) | Content fidelity, theme extraction, technical correctness | propose_themes, propose_items, edit_item, rebut_objection | retrieval over uploaded materials, equation validator |
| **Blueprint Architect** | Builds 2D content × Bloom matrix from CLOs + topics + exam spec | propose_blueprint, revise_blueprint | none |
| **Learning Outcomes Alignment** | Verifies each item maps to a CLO at the right cognitive level (constructive alignment) | object, suggest_realignment | Bloom verb classifier |
| **Item-Writing Specialist** | Mechanics: stem clarity, distractor quality, cue leakage, item-writing flaws | object, propose_edit | item-flaw checklist (rule-based) |
| **Accessibility Expert** | Universal design, plain language where appropriate, alt-text, accommodation variants | object, propose_edit, generate_variant | readability scorer, jargon detector |
| **Psychometrician** | Difficulty distribution, discrimination estimate, exam-level reliability heuristics | object, estimate_difficulty, flag_imbalance | none |
| **Adversarial Student** (formerly Skeptic) | Test-wiseness exploits, guessing strategies, prompt-injection if AI tools allowed | attempt_solve_without_content, object | none — operates only on item text |
| **Moderator** | Orchestration; not an LLM agent, deterministic | route, resolve_by_policy, enforce_quotas | trade-off policy engine |

**Anti-sycophancy rule**: critic agents (LOA, IWS, Accessibility, Psychometrician, Adversarial Student) must produce at least N objections per epoch (configurable, default 1 per item per critic in epoch 1, decaying). Empty critique is rejected by the Moderator and re-prompted.

---

## 4. Orchestration: The Epoch Loop

```
Phase 0 — Intake
  ingest materials → chunk + embed → vector store
  parse course spec, exam spec, trade-off policy

Phase 1 — Blueprinting
  SME: extract candidate themes (ranked) from materials
  Blueprint Architect: propose blueprint (topic × Bloom matrix)
  → CHECKPOINT 1: professor reviews/edits blueprint  ← human-in-the-loop
  blueprint frozen

Phase 2 — Item Generation (Epoch 1)
  for each blueprint cell:
    SME proposes 1.5× target items (overgenerate)
    Item-Writing Specialist drafts cleanup pass
    LOA verifies CLO + Bloom alignment, drops misaligned
  → CHECKPOINT 2: professor reviews item bank ← human-in-the-loop

Phase 3 — Refinement Epochs (2..N)
  for epoch in 2..max_epochs:
    parallel critique:
      Accessibility Expert reviews all items
      Adversarial Student attempts each item using only test-wiseness
      Psychometrician scores difficulty + flags imbalance
      LOA + IWS re-check after any edits
    Moderator:
      collects objections
      routes to SME for rebuttal or edit
      applies trade-off policy when agents disagree
      logs everything to provenance
    check convergence: no critical/high objections for full epoch → exit
  → CHECKPOINT 3: professor reviews final draft ← human-in-the-loop

Phase 4 — Finalization
  Accessibility Expert generates required variants (extended-time, screen-reader, large-print)
  Psychometrician produces exam-level report (Bloom distribution, est. difficulty curve, CLO coverage heatmap)
  Export bundle
```

**Concurrency**: Phase 3 critics run in parallel per item; SME rebuttals serialized per item; Moderator is single-threaded.

**Stop conditions**: convergence rule met, max_epochs reached, or professor halts via UI.

---

## 5. Moderator Logic (Disagreement Resolution)

Deterministic, not an LLM. Resolves objections using the professor-specified trade-off policy.

```
for objection in open_objections:
  if objection.severity == "critical":
    force SME edit or item rejection
  elif conflict_exists(objection, other_objections):
    winner = priority_rank.first_match(involved_dimensions)
    log resolution rationale
  else:
    route to SME for response (accept_edit | rebut | defer)
  if rebutted and critic re-asserts → escalate to professor at next checkpoint
```

Every resolution writes a `ProvenanceEvent`. Rebut-reassert loops bounded to 2 rounds before escalation.

---

## 6. Retrieval & Grounding

- All uploaded materials chunked (semantic chunking, ~800 tokens), embedded, stored in local vector DB (Chroma or LanceDB — your call)
- SME item proposals require ≥1 `source_ref` citing chunk IDs; items without citations are auto-rejected
- A separate **Grounding Verifier** (lightweight LLM call, not a full agent) checks that the item's claimed answer is actually supported by the cited chunks. Failures route back to SME.
- Prior exams: indexed separately, used only for style retrieval, never returned as source for new items (flag check on chunk metadata)

---

## 7. Streamlit UI Surfaces

| Screen | Purpose |
|---|---|
| **Upload** | drop materials, parse course spec form, exam spec form, trade-off sliders |
| **Blueprint Review** | matrix view, editable cells, professor approves/edits — checkpoint 1 |
| **Item Bank Review** | item cards with provenance, accept/reject/edit, filter by CLO/Bloom/topic — checkpoint 2 |
| **Refinement Live View** | per-epoch stream of objections, edits, resolutions; expandable per item |
| **Final Review** | full exam preview, variants tab, coverage heatmaps, exam-level report — checkpoint 3 |
| **Export** | DOCX, PDF, QTI, LaTeX, JSON-with-provenance |

State: Streamlit `session_state` for UI, but durable state in SQLite (one row per item version, one row per objection, one row per provenance event). Resumable across sessions.

---

## 8. Agent Framework Choice

Build on **LangGraph** (best fit: explicit state machine, conditional edges, durable checkpointing, native human-in-the-loop interrupts) or **CrewAI** (simpler but less control over the moderator logic). Strong recommendation: LangGraph — the epoch loop and checkpoint pattern are exactly what it's designed for.

Models: Claude Opus for SME and Adversarial Student (reasoning-heavy), Claude Sonnet for everything else, with model choice per-agent configurable.

---

## 9. Export Bundle

Per exam:
- `exam.docx` (primary, faculty-editable)
- `exam_variants/*.docx` (accessibility variants)
- `answer_key.docx`
- `rubrics.docx`
- `exam.qti.zip` (Canvas/Blackboard import)
- `exam_report.pdf` (blueprint, coverage, Bloom distribution, est. difficulty curve)
- `provenance.json` (full audit trail, defensible against grade appeals)
- `exam.tex` (optional)

---

## 10. Build Order (Suggested)

1. Data models + SQLite schema + Streamlit upload screen
2. Ingestion + retrieval (Chroma + Grounding Verifier)
3. SME + Blueprint Architect, end-to-end through checkpoint 1
4. Item generation loop (SME + IWS + LOA), end-to-end through checkpoint 2
5. Critic agents one at a time: Accessibility → Adversarial Student → Psychometrician
6. Moderator + trade-off policy engine
7. Refinement live view UI
8. Variants generator + export bundle
9. Provenance UI and grade-appeal export view
