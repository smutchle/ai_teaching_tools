"""Adversarial Student agent.

Attempts to answer items using only test-wiseness — no access to the source
materials, no access to the answer key, no access to the rubric. The point is
to surface items that reward guessing strategies rather than discriminating
prepared from unprepared students.

Hard rule: this agent must NEVER see answer_key, rubric, source_refs, or any
field that leaks the answer. The redaction happens here in the agent, not in
the caller — defense in depth.
"""

import json
from typing import Any, ClassVar

from models import (
    Item,
    ItemObjections,
    ItemObjectionsBatch,
    ObjectionDraft,
    ObjectionDraftList,
    SolveAttempt,
)

from agents.base import BaseAgent
from agents.critique_batch import normalize_critique_batch as _normalize_critique_batch


_STUDENT_VISIBLE_FIELDS = {"id", "type", "stem", "options", "points"}


def _student_view(item: Item) -> str:
    """Render only the fields a student would see on the exam paper.

    Strips answer_key, rubric, source_refs, clo_refs, topic_refs, bloom_level,
    knowledge_type, difficulty_est, accessibility_notes, discrimination_est,
    status, provenance — everything that would let the adversary cheat.
    """
    visible: dict[str, Any] = item.model_dump(include=_STUDENT_VISIBLE_FIELDS)
    return json.dumps(visible, indent=2, default=str)


class AdversarialStudentAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "adversarial_student"

    def attempt_solve(self, item: Item) -> SolveAttempt:
        prompt = (
            f"Attempt to answer item {item.id} using ONLY test-wiseness strategies. "
            "You have no access to source materials, no answer key, no rubric, and "
            "no information about the construct being measured.\n\n"
            "Test-wiseness strategies include: length cues (the longest option is "
            "often correct in poorly-written MCQ), grammatical agreement with the "
            "stem, absolute-language elimination (options containing 'always' or "
            "'never' are usually wrong), convergence (the option that overlaps most "
            "with other options is often correct), 'all of the above' patterns, "
            "stem-option word matching, format consistency cues, and outlier "
            "elimination (one option that differs in length, formatting, or "
            "specificity).\n\n"
            "Choose the answer you would pick. Set confidence to how strongly the "
            "test-wiseness signal points at that answer (0.0 = pure guess, 1.0 = "
            "the cues unambiguously identify the answer). Set exploit_used to the "
            "specific strategy you relied on. If no test-wiseness strategy applies, "
            "set chosen_answer to a guess, confidence to a low value (0.1–0.3), and "
            "exploit_used to 'none — pure guess'.\n\n"
            "High confidence is the signal the system uses to flag exploitable "
            "items. Be calibrated: do not inflate confidence for items where the "
            "cues are weak.\n\n"
            f"--- ITEM (id={item.id}) — STUDENT VIEW ONLY ---\n{_student_view(item)}"
        )
        return self._invoke(prompt, SolveAttempt)

    _CRITIQUE_GUIDANCE = (
        "Inspect each item from a test-taker's perspective. Raise objections "
        "about construction patterns that reward test-wiseness over "
        "preparation. Use category strings from the catalogue in your "
        "constitution.\n\n"
        "You operate on the same redacted view as in attempt_solve — no "
        "answer key, no rubric, no source refs, no metadata. Your critique "
        "is grounded in what a test-wise student would notice when looking "
        "at the item.\n\n"
        "Stay in your lane: your output is about exploitability, not content "
        "(SME), mechanics in the abstract (IWS handles those), alignment "
        "(LOA), accessibility (Accessibility Expert), or calibration "
        "(Psychometrician). An item-writing flaw that is not exploitable by "
        "a test-wise student is not your concern."
    )

    def critique(self, item: Item) -> list[ObjectionDraft]:
        """Raise objections about exploitable construction patterns.

        Distinct from IWS critique: IWS catalogues item-writing flaws as
        construction defects; you catalogue the exploits those defects enable
        from the test-taker's side. Same item may surface in both critiques —
        the Moderator handles the deduplication.
        """
        prompt = (
            f"{self._CRITIQUE_GUIDANCE}\n\nSet `target` to the item id "
            f"exactly as given. If you cannot identify any exploitable "
            f"pattern, return an empty list — but examine first.\n\n"
            f"--- ITEM (id={item.id}) — STUDENT VIEW ONLY ---\n{_student_view(item)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections

    def critique_batch(self, items: list[Item]) -> list[ItemObjections]:
        """Batch form: scan all items at once using only student-view fields.

        Critical: every item still goes through `_student_view` so the
        redaction of answer_key / rubric / source_refs / metadata holds
        even under batching.
        """
        if not items:
            return []
        item_blocks = [
            f"--- ITEM (id={it.id}) — STUDENT VIEW ONLY ---\n{_student_view(it)}"
            for it in items
        ]
        prompt = (
            f"{self._CRITIQUE_GUIDANCE}\n\n"
            f"Inspect ALL {len(items)} items below. For each item return an "
            "entry in the response with `item_id` set to the item id exactly "
            "as given and `objections` containing your findings for THAT item. "
            "Include every item even if you have no concerns — return "
            "`objections: []` for clean items. Each ObjectionDraft's `target` "
            "should equal its containing item's id.\n\n"
            + "\n\n".join(item_blocks)
        )
        batch = self._invoke(prompt, ItemObjectionsBatch)
        return _normalize_critique_batch(batch, items)
