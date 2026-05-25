"""Blueprint Architect agent.

Builds the topic-by-Bloom-level matrix that governs coverage before any items
are written. Reconciles three inputs: the course specification (planned CLOs,
topics, topic weights), the exam specification (item-type counts, point budget,
difficulty distribution), and the themes the SME extracted from the actual
uploaded materials.

Also emits a per-item SlotPlan: one ItemSlot for every item the exam will
contain, with item_type and difficulty pinned exactly to the spec. The
moderator validates the slot histogram and asks the BA to fix any drift
before Phase 2.
"""

from typing import ClassVar

from models import (
    Blueprint,
    CourseSpec,
    Difficulty,
    ExamSpec,
    ItemSlot,
    ItemType,
    SlotPlan,
    Theme,
)

from agents.base import BaseAgent


def _format_themes(themes: list[Theme]) -> str:
    if not themes:
        return "(no themes provided)"
    return "\n".join(
        f"- rank={t.rank} | id={t.id}\n  {t.text}\n  ({len(t.source_refs)} source chunks)"
        for t in themes
    )


def _format_target_histograms(exam_spec: ExamSpec) -> str:
    """Spell out the exact per-type and per-difficulty counts the SlotPlan must hit."""
    type_counts = exam_spec.target_item_type_counts()
    diff_counts = exam_spec.target_difficulty_counts()
    n = exam_spec.total_item_count()
    type_lines = "\n".join(
        f"    {t.value}: {c}"
        for t, c in type_counts.items()
        if c > 0
    )
    diff_lines = "\n".join(f"    {d.value}: {c}" for d, c in diff_counts.items())
    return (
        f"TOTAL SLOTS: exactly {n}\n"
        f"  Per item_type (must match exactly):\n{type_lines}\n"
        f"  Per difficulty (must match exactly):\n{diff_lines}"
    )


def _histogram_mismatch(
    plan: SlotPlan, exam_spec: ExamSpec,
) -> list[str]:
    """Return human-readable deltas where the SlotPlan diverges from the spec.

    Empty list ⇒ the plan is histogram-correct. Used by the agent-level retry
    loop to re-prompt with a precise diff rather than a generic 'try again'.
    """
    issues: list[str] = []
    target_total = exam_spec.total_item_count()
    if len(plan.slots) != target_total:
        issues.append(
            f"slot count: got {len(plan.slots)}, expected {target_total}"
        )
    # Per-type
    target_types = exam_spec.target_item_type_counts()
    actual_types: dict[ItemType, int] = {t: 0 for t in target_types}
    for s in plan.slots:
        actual_types[s.item_type] = actual_types.get(s.item_type, 0) + 1
    for t, want in target_types.items():
        got = actual_types.get(t, 0)
        if got != want:
            issues.append(f"item_type {t.value}: got {got}, expected {want}")
    # Per-difficulty
    target_diffs = exam_spec.target_difficulty_counts()
    actual_diffs: dict[Difficulty, int] = {d: 0 for d in target_diffs}
    for s in plan.slots:
        actual_diffs[s.difficulty] = actual_diffs.get(s.difficulty, 0) + 1
    for d, want in target_diffs.items():
        got = actual_diffs.get(d, 0)
        if got != want:
            issues.append(f"difficulty {d.value}: got {got}, expected {want}")
    return issues


def _local_repair_slot_histograms(plan: SlotPlan, exam_spec: ExamSpec) -> SlotPlan:
    """Deterministic last-ditch repair when BA can't get the histograms right.

    Preserves the BA's (topic_id, bloom_level, points, clo_refs) allocation
    per slot and only reassigns ``item_type`` and ``difficulty`` so the
    overall histograms match the spec exactly. If slot count is wrong, pads
    by cloning the highest-priority cell's slot or trims the tail.

    This is a safety net — when the LLM produces, e.g., 5 MCQs instead of 3,
    we still ship a histogram-correct exam rather than blocking the run.
    """
    target_total = exam_spec.total_item_count()
    slots = list(plan.slots)

    # 1. Fix slot count first. Pad by cloning the last slot; trim from the tail.
    if len(slots) < target_total and slots:
        template = slots[-1]
        while len(slots) < target_total:
            slots.append(template.model_copy())
    elif len(slots) > target_total:
        slots = slots[:target_total]

    # 2. Build the target multiset of types and difficulties, in a stable order.
    type_pool: list[ItemType] = []
    for t, c in exam_spec.target_item_type_counts().items():
        type_pool.extend([t] * c)
    diff_pool: list[Difficulty] = []
    for d, c in exam_spec.target_difficulty_counts().items():
        diff_pool.extend([d] * c)

    # 3. Assign by slot index. Order is deterministic so reruns are reproducible.
    out: list[ItemSlot] = []
    for i, s in enumerate(slots):
        out.append(
            s.model_copy(update={
                "item_type": type_pool[i] if i < len(type_pool) else s.item_type,
                "difficulty": diff_pool[i] if i < len(diff_pool) else s.difficulty,
            })
        )
    return SlotPlan(slots=out)


def _normalize_slot_ids(plan: SlotPlan) -> SlotPlan:
    """Stamp deterministic slot_ids (slot_0001 …) so downstream provenance is stable.

    Whatever the model emits gets renumbered — the LLM has no reason to pick
    semantically meaningful slot ids and inconsistent ones make logs noisier.
    """
    renumbered: list[ItemSlot] = []
    for i, s in enumerate(plan.slots, start=1):
        renumbered.append(s.model_copy(update={"slot_id": f"slot_{i:04d}"}))
    return SlotPlan(slots=renumbered)


class BlueprintArchitectAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "blueprint_architect"

    # Max number of histogram-repair re-prompts before falling back to a
    # deterministic local fix. Two is enough in practice; the BA reliably
    # closes a small histogram delta on the second attempt.
    _MAX_HISTOGRAM_REPAIRS: ClassVar[int] = 2

    def propose_blueprint(
        self,
        course_spec: CourseSpec,
        exam_spec: ExamSpec,
        themes: list[Theme],
    ) -> Blueprint:
        prompt = (
            "Propose a blueprint for this exam: a list of (topic, Bloom level) cells "
            "with target item counts and point values, plus a per-item slot_plan "
            "listing one ItemSlot per planned exam item. The blueprint is the single "
            "artifact that governs coverage; downstream agents propose and critique "
            "items against it.\n\n"
            "Reconcile three inputs:\n"
            "  - The course spec: planned CLOs, topics with relative weights, and "
            "guiding principles.\n"
            "  - The exam spec: total points, time budget, item-type counts, target "
            "difficulty distribution, accommodations.\n"
            "  - The themes the SME extracted from the actual uploaded materials: "
            "what the materials actually cover, ranked by centrality.\n\n"
            "Constraints the cells must satisfy:\n"
            "  - Sum of cell target_points equals exam_spec.total_points.\n"
            "  - Sum of cell target_item_counts equals the total of "
            "exam_spec.item_type_counts.\n"
            "  - Every CLO in course_spec appears in at least one cell's clo_refs.\n"
            "  - Topic weighting across cells respects course_spec.topics weights "
            "(treat weights as the planned distribution; deviate only when themes "
            "show the materials cannot support that distribution, and record the "
            "deviation in coverage_check.warnings).\n"
            "  - Bloom distribution reflects the level of the CLOs (do not let cells "
            "drift toward REMEMBER and UNDERSTAND).\n"
            "  - Include a coverage_check with the actual covered/uncovered lists "
            "and any warnings.\n\n"
            "Constraints the slot_plan must satisfy EXACTLY (no flex):\n"
            f"{_format_target_histograms(exam_spec)}\n"
            "  - One ItemSlot per planned exam item. Each slot has slot_id "
            "(e.g. 'slot_0001'), topic_id, topic_name, bloom_level, item_type, "
            "difficulty, points, clo_refs.\n"
            "  - Every slot's (topic_id, bloom_level) must match one of the cells "
            "you produced above; topic_name should match that cell's topic_name.\n"
            "  - clo_refs on a slot must be a subset of the matching cell's clo_refs.\n"
            "  - The per-item_type and per-difficulty counts across slots must hit "
            "the exact totals above. Do not over- or under-fill any bucket.\n"
            "  - Distribute slots across the cells you produced so each cell's "
            "target_item_count is honored (sum of slots per cell equals that "
            "cell's target_item_count).\n"
            "  - Choose item_type per slot based on what assesses the cell's "
            "Bloom+topic well (e.g. multi-step PROBLEM for apply/analyze on a "
            "quantitative topic; MCQ for understand on a definitional one). The "
            "totals are fixed; you decide which cells each type lives in.\n\n"
            f"--- COURSE SPEC ---\n{course_spec.model_dump_json(indent=2)}\n\n"
            f"--- EXAM SPEC ---\n{exam_spec.model_dump_json(indent=2)}\n\n"
            f"--- SME THEMES (ranked) ---\n{_format_themes(themes)}"
        )
        blueprint = self._invoke(prompt, Blueprint)

        # Histogram-repair retry loop. Most of the time the BA gets it right
        # on the first call; when it doesn't, hand it the precise deltas and
        # ask it to fix only the slot_plan field (cells stay as-is).
        for _attempt in range(self._MAX_HISTOGRAM_REPAIRS):
            mismatches = _histogram_mismatch(blueprint.slot_plan, exam_spec)
            if not mismatches:
                break
            repair_prompt = (
                "Your slot_plan does not match the ExamSpec exactly. Fix the "
                "slot_plan and return the SAME blueprint (cells + coverage_check "
                "unchanged) with a corrected slot_plan. The mismatches are:\n"
                + "\n".join(f"  - {m}" for m in mismatches)
                + "\n\nRequired histograms:\n"
                + _format_target_histograms(exam_spec)
                + "\n\n--- CURRENT BLUEPRINT ---\n"
                + blueprint.model_dump_json(indent=2)
            )
            blueprint = self._invoke(repair_prompt, Blueprint)

        # Deterministic local fallback: if the BA still hasn't converged after
        # the retry budget, reassign item_type and difficulty across the slots
        # the BA produced so the histograms hit the spec exactly. This keeps
        # the topic/Bloom allocation the BA chose while guaranteeing the type
        # and difficulty contracts ship.
        mismatches = _histogram_mismatch(blueprint.slot_plan, exam_spec)
        if mismatches:
            repaired = _local_repair_slot_histograms(blueprint.slot_plan, exam_spec)
            blueprint = blueprint.model_copy(update={"slot_plan": repaired})

        blueprint = blueprint.model_copy(
            update={"slot_plan": _normalize_slot_ids(blueprint.slot_plan)}
        )
        return blueprint

    def revise_blueprint(
        self,
        blueprint: Blueprint,
        course_spec: CourseSpec,
        exam_spec: ExamSpec,
        faculty_feedback: str,
    ) -> Blueprint:
        """Revise a blueprint after Checkpoint 1 faculty review.

        Faculty edits may be inline cell changes in the UI, or free-text feedback
        captured into faculty_feedback. The agent's job is to reconcile the
        feedback with the spec constraints — do not silently drop a constraint
        to satisfy a feedback line; flag the conflict in coverage_check.warnings.
        """
        prompt = (
            "Revise this blueprint in response to faculty feedback. Preserve the "
            "constraints listed in your constitution (point total, item count total, "
            "CLO coverage, topic weighting, Bloom distribution). If the feedback "
            "directly conflicts with a constraint, do not silently drop the "
            "constraint — apply the feedback to the extent possible and record the "
            "conflict in coverage_check.warnings so the next checkpoint surfaces it.\n\n"
            f"--- CURRENT BLUEPRINT ---\n{blueprint.model_dump_json(indent=2)}\n\n"
            f"--- COURSE SPEC ---\n{course_spec.model_dump_json(indent=2)}\n\n"
            f"--- EXAM SPEC ---\n{exam_spec.model_dump_json(indent=2)}\n\n"
            f"--- FACULTY FEEDBACK ---\n{faculty_feedback}"
        )
        return self._invoke(prompt, Blueprint)
