"""Form widgets for CourseSpec / ExamSpec / TradeOffPolicy.

Each builder reads from and writes to `st.session_state` under a stable key
so values survive reruns. Returns the in-progress dict; the caller validates
by attempting `CourseSpec.model_validate(...)` etc. at submit time.

Dynamic lists (CLOs, Topics) use add/remove buttons that mutate session
state and `st.rerun()`.

CLO and Topic `id` fields are not exposed in the UI — they're internal refs
that the user has no reason to type. We slug-generate them from the text /
name at the end of each render and dedupe within the list. Existing ids
loaded from test_data JSON are preserved so reloading defaults is stable.
"""

from __future__ import annotations

import re
from typing import Any

import streamlit as st


_SLUG_BAD = re.compile(r"[^\w\s-]")
_SLUG_WS = re.compile(r"[-\s]+")


def _slugify(text: str, *, max_len: int = 28) -> str:
    s = _SLUG_BAD.sub("", text.lower())
    s = _SLUG_WS.sub("_", s).strip("_")
    return s[:max_len].rstrip("_")


def _finalize_ids(items: list[dict[str, Any]], *, prefix: str, text_field: str) -> None:
    """Fill in any blank `id` from a slug of `text_field`, deduping within the
    list. Existing non-empty ids are preserved unchanged."""
    used: set[str] = {it["id"] for it in items if it.get("id")}
    for i, it in enumerate(items, start=1):
        if it.get("id"):
            continue
        base = _slugify(it.get(text_field, "") or "")
        candidate = f"{prefix}_{base}" if base else f"{prefix}_{i}"
        n = 2
        while candidate in used:
            candidate = f"{prefix}_{base}_{n}" if base else f"{prefix}_{i}_{n}"
            n += 1
        it["id"] = candidate
        used.add(candidate)


_BLOOM_OPTIONS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]
_KTYPE_OPTIONS = ["factual", "conceptual", "procedural", "metacognitive"]
_EXAM_TYPE_OPTIONS = ["midterm", "final", "quiz", "qualifying"]
_ACCOMMODATION_OPTIONS = ["extended_time", "screen_reader", "large_print"]
_POLICY_DIMENSIONS = [
    "content_fidelity", "cognitive_alignment", "accessibility",
    "discrimination", "brevity",
]


# ---- CourseSpec ----------------------------------------------------------

def _blank_clo() -> dict[str, Any]:
    return {"id": "", "text": "", "bloom_level": "apply", "knowledge_type": "procedural"}


def _blank_topic() -> dict[str, Any]:
    return {"id": "", "name": "", "weight": 0.0, "source_refs": []}


def render_course_spec_form(state_key: str) -> dict[str, Any]:
    spec = st.session_state[state_key]

    st.markdown("#### Materials Learning Objectives (MLOs)")
    st.caption(
        "Scope these to the uploaded materials, not the whole course. "
        "Each MLO becomes at least one blueprint cell — the Bloom level "
        "constrains how the item must be written, and the text is what "
        "students will be tested against. "
        "If an MLO isn't supported by the uploaded PDF, the Phase-4 audit "
        "flags it as **uncovered**."
    )

    clos = spec.setdefault("clos", [])
    # Column layout: text · bloom · knowledge type · remove
    header = st.columns([5, 1.2, 1.2, 0.5])
    header[0].caption("MLO text")
    header[1].caption("Bloom level")
    header[2].caption("Knowledge type")
    for i, clo in enumerate(clos):
        cols = st.columns([5, 1.2, 1.2, 0.5])
        clo["text"] = cols[0].text_input(
            "text", value=clo.get("text", ""), key=f"clo_text_{i}",
            label_visibility="collapsed",
            placeholder="e.g. Apply Hess's law to compute standard enthalpy changes...",
        )
        clo["bloom_level"] = cols[1].selectbox(
            "bloom", _BLOOM_OPTIONS, key=f"clo_bloom_{i}",
            index=_BLOOM_OPTIONS.index(clo.get("bloom_level", "apply")),
            label_visibility="collapsed",
        )
        clo["knowledge_type"] = cols[2].selectbox(
            "ktype", _KTYPE_OPTIONS, key=f"clo_ktype_{i}",
            index=_KTYPE_OPTIONS.index(clo.get("knowledge_type", "procedural")),
            label_visibility="collapsed",
        )
        if cols[3].button("✕", key=f"clo_rm_{i}", help="Remove this MLO"):
            clos.pop(i)
            st.rerun()
    if st.button("➕ Add MLO", key="add_clo"):
        clos.append(_blank_clo())
        st.rerun()

    st.markdown("#### Materials topics")
    st.caption(
        "Subject areas covered by the uploaded materials — the labels under "
        "which content is grouped. The pipeline uses each topic name as a "
        "retrieval query against the PDF, so topics that *match real language "
        "in the materials* retrieve better chunks and produce better items.\n\n"
        "**How this differs from \"themes\":** in Phase 1 the SME agent also "
        "extracts *themes* — exam-item-friendly framings of what the corpus "
        "actually emphasizes. Themes are auto-discovered from your PDF; you "
        "don't supply them. The Blueprint Architect reconciles your topics "
        "against the SME's themes and flags any topic the materials don't "
        "actually support."
    )
    st.caption(
        "Weights are relative — they govern how the blueprint distributes "
        "points across topics. Weights are normalized at use time; they do "
        "not need to sum to 1.0 exactly."
    )

    topics = spec.setdefault("topics", [])
    t_header = st.columns([5, 1, 0.5])
    t_header[0].caption("Topic name")
    t_header[1].caption("Weight")
    for i, t in enumerate(topics):
        cols = st.columns([5, 1, 0.5])
        t["name"] = cols[0].text_input(
            "name", value=t.get("name", ""), key=f"topic_name_{i}",
            label_visibility="collapsed",
            placeholder="e.g. Hess's law and standard enthalpies",
        )
        t["weight"] = cols[1].number_input(
            "weight", min_value=0.0, max_value=10.0,
            value=float(t.get("weight", 0.0)), step=0.05,
            key=f"topic_weight_{i}", label_visibility="collapsed",
        )
        # source_refs are populated by ingestion, not the user — keep them on
        # the dict but don't expose in the form.
        t.setdefault("source_refs", [])
        if cols[2].button("✕", key=f"topic_rm_{i}", help="Remove this topic"):
            topics.pop(i)
            st.rerun()
    if st.button("➕ Add topic", key="add_topic"):
        topics.append(_blank_topic())
        st.rerun()

    st.markdown("#### Guiding principles")
    spec["guiding_principles"] = st.text_area(
        "Guiding principles",
        value=spec.get("guiding_principles", ""),
        height=120,
        label_visibility="collapsed",
        help="Free-text instructions that shape every agent's behavior — "
             "e.g., 'emphasize quantitative reasoning, no rote definitions'.",
    )

    # Auto-generate any missing ids from text/name. Existing ids from
    # test_data defaults are preserved. Users never see this layer.
    _finalize_ids(spec.get("clos", []), prefix="clo", text_field="text")
    _finalize_ids(spec.get("topics", []), prefix="topic", text_field="name")
    return spec


# ---- ExamSpec ------------------------------------------------------------

def render_exam_spec_form(state_key: str) -> dict[str, Any]:
    spec = st.session_state[state_key]

    cols = st.columns(3)
    spec["exam_type"] = cols[0].selectbox(
        "Exam type", _EXAM_TYPE_OPTIONS,
        index=_EXAM_TYPE_OPTIONS.index(spec.get("exam_type", "quiz")),
    )
    spec["total_points"] = cols[1].number_input(
        "Total points", min_value=1, max_value=500,
        value=int(spec.get("total_points", 100)), step=1,
    )
    spec["time_budget_minutes"] = cols[2].number_input(
        "Time budget (min)", min_value=5, max_value=480,
        value=int(spec.get("time_budget_minutes", 60)), step=5,
    )

    st.markdown("##### Item type counts")
    counts = spec.setdefault("item_type_counts", {})
    cnt_cols = st.columns(5)
    counts["mcq"] = cnt_cols[0].number_input(
        "MCQ", min_value=0, max_value=200,
        value=int(counts.get("mcq", 0)), step=1,
    )
    counts["short_answer"] = cnt_cols[1].number_input(
        "Short answer", min_value=0, max_value=200,
        value=int(counts.get("short_answer", 0)), step=1,
    )
    counts["problem"] = cnt_cols[2].number_input(
        "Problem", min_value=0, max_value=200,
        value=int(counts.get("problem", 0)), step=1,
    )
    counts["derivation"] = cnt_cols[3].number_input(
        "Derivation", min_value=0, max_value=200,
        value=int(counts.get("derivation", 0)), step=1,
    )
    counts["data_interp"] = cnt_cols[4].number_input(
        "Data interp", min_value=0, max_value=200,
        value=int(counts.get("data_interp", 0)), step=1,
    )

    st.markdown("##### Difficulty distribution")
    st.caption("Target fraction of items at each difficulty. Should sum to ~1.0.")
    diff = spec.setdefault("difficulty_distribution", {})
    diff_cols = st.columns(3)
    diff["easy_ratio"] = diff_cols[0].number_input(
        "Easy", min_value=0.0, max_value=1.0,
        value=float(diff.get("easy_ratio", 0.25)), step=0.05, format="%.2f",
    )
    diff["medium_ratio"] = diff_cols[1].number_input(
        "Medium", min_value=0.0, max_value=1.0,
        value=float(diff.get("medium_ratio", 0.50)), step=0.05, format="%.2f",
    )
    diff["hard_ratio"] = diff_cols[2].number_input(
        "Hard", min_value=0.0, max_value=1.0,
        value=float(diff.get("hard_ratio", 0.25)), step=0.05, format="%.2f",
    )
    s = diff["easy_ratio"] + diff["medium_ratio"] + diff["hard_ratio"]
    if abs(s - 1.0) > 0.02:
        st.warning(f"Difficulty ratios sum to {s:.2f}, not 1.00.")

    spec["accommodations_required"] = st.multiselect(
        "Accommodations required",
        _ACCOMMODATION_OPTIONS,
        default=spec.get("accommodations_required", []),
        help="A variant of every surviving item will be generated for each kind selected.",
    )

    spec["latex_required"] = st.toggle(
        "LaTeX required", value=bool(spec.get("latex_required", True)),
        help="When on, agents are instructed to use LaTeX for ALL math and "
             "discipline-specific notation (no raw Unicode like Δ, °, →).",
    )
    # figure_support stays on the model (still consumed by the moderator's
    # notation directive) but the UI no longer exposes a toggle — figure
    # references in stems are allowed by default.
    spec["figure_support"] = True
    return spec


# ---- TradeOffPolicy ------------------------------------------------------

def render_policy_form(state_key: str) -> dict[str, Any]:
    policy = st.session_state[state_key]

    st.caption("Order the trade-off dimensions from highest to lowest priority. "
               "When two agents disagree on overlapping dimensions, the higher-"
               "ranked dimension wins.")
    current = policy.get("priority_rank", _POLICY_DIMENSIONS[:])
    # Pad / trim to length 5 with sensible defaults.
    while len(current) < 5:
        for d in _POLICY_DIMENSIONS:
            if d not in current:
                current.append(d)
                break
    current = current[:5]

    new_rank: list[str] = []
    rank_cols = st.columns(5)
    for i in range(5):
        # Each slot can pick any dimension, but we validate uniqueness on submit.
        default = current[i]
        if default not in _POLICY_DIMENSIONS:
            default = _POLICY_DIMENSIONS[i]
        with rank_cols[i]:
            choice = st.selectbox(
                f"Rank {i+1}", _POLICY_DIMENSIONS,
                index=_POLICY_DIMENSIONS.index(default),
                key=f"policy_rank_{i}",
            )
            new_rank.append(choice)
    policy["priority_rank"] = new_rank
    if len(set(new_rank)) != 5:
        dups = [d for d in new_rank if new_rank.count(d) > 1]
        st.error(f"Priority rank must use each dimension exactly once. Duplicates: {sorted(set(dups))}")

    policy["max_epochs"] = st.number_input(
        "Max refinement epochs",
        min_value=1, max_value=10,
        value=int(policy.get("max_epochs", 4)), step=1,
        help="Hard cap on Phase 3. Convergence (no critical/high open at end of epoch) exits earlier.",
    )
    policy.setdefault("convergence_rule", "no_critical_or_high_for_one_epoch")
    return policy
