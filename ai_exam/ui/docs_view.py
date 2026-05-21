"""Documentation page — Agents (markdown) + Diagrams (pre-rendered SVG).

Mermaid runtime rendering via CDN was unreliable in Streamlit (dagre layout
failures, empty iframes). We pre-render each phase diagram to SVG at design
time using `mmdc` (mermaid-cli) and inline the SVG via `st.markdown`. Edit
the `.mmd` sources under `ui/diagrams/` and re-run `ui/diagrams/render.sh`
to regenerate.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st


_DIAGRAMS_DIR = Path(__file__).resolve().parent / "diagrams"


# --------------------------------------------------------------------
# Tab 1 — agent roster as rendered markdown.
# --------------------------------------------------------------------

_AGENTS_MD = """\
# Agents & their roles by phase

The pipeline is **8 LLM agents plus a deterministic Moderator**. Agents are
single-purpose; the Moderator routes work between them, owns provenance, and
enforces phase / checkpoint transitions.

| Tier | Agents | Default model |
|---|---|---|
| **HIGH** (creative / heavy reasoning) | SME, Blueprint Architect, Adversarial Student | claude-sonnet-4-6 |
| **LOW** (verification / pattern-matching) | IWS, LOA, Grounding Verifier, Accessibility, Psychometrician | claude-haiku-4-5 |

Per-tier routing is set on the Run page; defaults can be edited in
`config.MODEL_REGISTRY`.

---

## SME — Subject Matter Expert · HIGH

Content fidelity. The only agent that **authors item content** end-to-end.

| Phase | Verb | What it does |
|---|---|---|
| 1 | `propose_themes(corpus, target_count)` | Reads the entire (batched) corpus, returns ranked themes — the SME's view of what the materials emphasize. Feeds the Blueprint Architect. |
| 2 | `propose_items(cell, chunks)` | For each blueprint cell, drafts 2× the target item count, grounded in cited chunks. |
| 3 | `rebut_objections(item, [Objection, …])` | One batched stance call (ACCEPT / REBUT / DEFER) per item across all non-critical objections raised against it. |
| 3 | `edit_item(item, objection)` | Re-writes the item to satisfy an accepted (or critical) objection. |

---

## Blueprint Architect · HIGH

Reconciles **three independent inputs** into one coverage matrix.

| Phase | Verb | What it does |
|---|---|---|
| 1 | `propose_blueprint(course_spec, exam_spec, themes)` | Outputs a list of `(topic, Bloom level)` cells with `target_item_count`, `target_points`, `clo_refs`. Enforces: each CLO appears in ≥1 cell, point total = exam_spec.total_points, Bloom distribution matches CLO levels. |
| 1 | `revise_blueprint(blueprint, feedback)` | (Reserved for human-in-the-loop CP1; not currently called.) |

---

## Item Writing Specialist (IWS) · LOW

Mechanical hygiene. Doesn't read the corpus, doesn't judge content.

| Phase | Verb | What it does |
|---|---|---|
| 2 | `cleanup(draft)` | Normalizes formatting, fixes obvious typos, standardizes equation spacing — pre-promotion. |
| 3 | `cleanup(item)` | Same, but called inside the re-verify cascade after every SME edit. |

---

## Learning Outcomes Alignment (LOA) · LOW

The Bloom + CLO gatekeeper. Reads what an item actually *demands* of a student vs. what the SME claims it does.

| Phase | Verb | What it does |
|---|---|---|
| 2 | `verify_alignment(item, clos)` | Pass/fail gate. If misaligned, the Moderator calls `suggest_realignment` next. |
| 2 | `suggest_realignment(item, clos)` | Recovery fallback. Recommends `remap_bloom` / `remap_clos` / `edit_item` / `reject`. The Moderator auto-applies `remap_bloom` and `remap_clos`. |
| 3 | `verify_alignment(item, clos)` | Re-verify cascade after every SME edit. |

---

## Grounding Verifier · LOW

Reads the cited chunks and asks: do they actually support this answer?

| Phase | Verb | What it does |
|---|---|---|
| 2 | `verify(item, cited_chunks)` | Final Phase-2 gate. Auto-fails if any `source_ref.chunk_id` is missing from the corpus. |
| 3 | `verify(item, cited_chunks)` | Same, in the re-verify cascade. |

---

## Accessibility Expert · LOW

Construct-irrelevant difficulty: text that's harder than the construct it measures.

| Phase | Verb | What it does |
|---|---|---|
| 3 | `critique_batch(items)` | One batched call per epoch; flags idioms, dense syntax, low-frequency non-technical vocabulary, culturally narrow examples. |
| 4 | `generate_variant(item, kind)` | Produces an accommodation variant (`extended_time` / `screen_reader` / `large_print`) per `exam_spec.accommodations_required`. |

---

## Adversarial Student · HIGH

Sees a **redacted student view** of every item (no answer key, no rubric, no source_refs). Finds construction patterns a test-wise student could exploit.

| Phase | Verb | What it does |
|---|---|---|
| 3 | `critique_batch(items)` | Length cues, grammatical agreement, "always/never" eliminations, convergence, format-consistency tells. |

The redaction is enforced by `_student_view()` in the agent itself — there's no path through the Moderator that hands the adversary unredacted content.

---

## Psychometrician · LOW

Item-level discrimination defects + exam-level coverage audit.

| Phase | Verb | What it does |
|---|---|---|
| 3 | `critique_batch(items)` | Item-level: predicts which items will fail to discriminate. |
| 4 | `audit_exam(draft, exam_spec)` | Exam-level: Bloom distribution, difficulty curve actual vs. target, CLO coverage with item + point counts, imbalance notes. Findings feed `exam_report.qmd`. |

---

## Moderator · deterministic, **not** an LLM

The orchestrator. Owns:

- Phase / checkpoint state machine
- Per-phase parallelism (Phase-2 cells, Phase-3 critique pass, per-item routing)
- Promotion (`ItemDraft` → `Item`, `ObjectionDraft` → `Objection`) with ids + provenance
- Convergence: `0 critical + high objections open → exit epoch loop`
- LOA realignment auto-apply
- Concurrency caps per provider, retry/repair on malformed JSON
"""


# --------------------------------------------------------------------
# Tab 2 — one Mermaid diagram per phase.
# Diagrams are kept compact (Mermaid struggles past ~30 nodes); the agent
# tab carries the prose detail.
# --------------------------------------------------------------------

_PHASE_DIAGRAMS: list[tuple[str, str]] = [
    ("Phase 0 — Intake (PDF → embeddings → Chroma)", "phase_0"),
    ("Phase 1 — Themes + Blueprint",                 "phase_1"),
    ("Phase 2 — Per-cell item generation",           "phase_2"),
    ("Phase 3 — Refinement epoch loop",              "phase_3"),
    ("Phase 4 — Audit + Variants + Export",          "phase_4"),
]


def _render_svg(stem: str) -> None:
    """Inline the pre-rendered SVG for `phase_<stem>.svg`."""
    svg_path = _DIAGRAMS_DIR / f"{stem}.svg"
    if not svg_path.exists():
        st.error(
            f"Diagram missing: `{svg_path.relative_to(_DIAGRAMS_DIR.parent)}`. "
            f"Run `ui/diagrams/render.sh` to regenerate."
        )
        return
    svg = svg_path.read_text(encoding="utf-8")
    # Inline SVG directly. Streamlit's CommonMark renderer will pass through
    # raw HTML when unsafe_allow_html=True; this keeps the diagram crisp at
    # any zoom level (no rasterization, no CDN, no JS).
    st.markdown(
        f'<div style="text-align:center;padding:8px">{svg}</div>',
        unsafe_allow_html=True,
    )


def render_docs_page() -> None:
    st.title("Documentation")
    tab_agents, tab_diagrams = st.tabs(["Agents", "Diagrams"])

    with tab_agents:
        st.markdown(_AGENTS_MD)

    with tab_diagrams:
        st.caption(
            "Flowcharts per pipeline phase. Phase 3 carries the epoch loop — "
            "each epoch is one full critique + routing + reverify cycle, "
            "exiting early when no critical/high severity objections remain. "
            "Sources are `ui/diagrams/phase_*.mmd`; re-render with "
            "`ui/diagrams/render.sh` after edits."
        )
        for title, stem in _PHASE_DIAGRAMS:
            st.subheader(title)
            _render_svg(stem)
