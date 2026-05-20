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
    ObjectionDraft,
    ObjectionDraftList,
    SolveAttempt,
)

from agents.base import BaseAgent


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

    def critique(self, item: Item) -> list[ObjectionDraft]:
        """Raise objections about exploitable construction patterns.

        Distinct from IWS critique: IWS catalogues item-writing flaws as
        construction defects; you catalogue the exploits those defects enable
        from the test-taker's side. Same item may surface in both critiques —
        the Moderator handles the deduplication.
        """
        prompt = (
            f"Inspect item {item.id} from a test-taker's perspective. Raise "
            "objections about construction patterns that reward test-wiseness over "
            "preparation. Use category strings from the catalogue in your "
            "constitution. Set target to the item id exactly as given.\n\n"
            "You operate on the same redacted view as in attempt_solve — no answer "
            "key, no rubric, no source refs, no metadata. Your critique is grounded "
            "in what a test-wise student would notice when looking at the item.\n\n"
            "Stay in your lane: your output is about exploitability, not content "
            "(SME), mechanics in the abstract (IWS handles those), alignment (LOA), "
            "accessibility (Accessibility Expert), or calibration (Psychometrician). "
            "An item-writing flaw that is not exploitable by a test-wise student is "
            "not your concern.\n\n"
            "If you cannot identify any exploitable pattern, return an empty list — "
            "but examine first.\n\n"
            f"--- ITEM (id={item.id}) — STUDENT VIEW ONLY ---\n{_student_view(item)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections
