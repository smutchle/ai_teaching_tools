"""Psychometrician agent.

Estimates per-item difficulty, raises objections about items with construction
patterns that predict poor discrimination, and audits the draft exam at the
exam level (Bloom distribution, difficulty curve, CLO coverage, imbalance
flags).
"""

from typing import ClassVar

from models import (
    DifficultyEstimate,
    ExamAudit,
    ExamDraft,
    ExamSpec,
    Item,
    ObjectionDraft,
    ObjectionDraftList,
)

from agents.base import BaseAgent


class PsychometricianAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "psychometrician"

    def estimate_difficulty(self, item: Item) -> DifficultyEstimate:
        prompt = (
            f"Estimate the difficulty of item {item.id} for the target population "
            "(students in the course for which this exam is being built). Use the "
            "operational definitions in your constitution. Provide a confidence "
            "score reflecting how strongly the item-level signals point at the "
            "chosen difficulty (0.0 = guess, 1.0 = high certainty).\n\n"
            "Difficulty is about the *predicted fraction of the target population* "
            "that would answer correctly, not about how complex the topic feels in "
            "the abstract. An apply-level item on a heavily-taught procedure is "
            "easy for that population even if the procedure is conceptually deep.\n\n"
            f"--- ITEM (id={item.id}) ---\n{item.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, DifficultyEstimate)

    def critique(self, item: Item) -> list[ObjectionDraft]:
        prompt = (
            f"Inspect item {item.id} for psychometric defects: construction patterns "
            "that predict poor discrimination or that mis-target the cell's claimed "
            "difficulty. Use category strings from the catalogue in your "
            "constitution. Set target to the item id exactly as given.\n\n"
            "Stay in your lane: do not raise objections about content (SME), "
            "mechanics (IWS), alignment (LOA), or accessibility (Accessibility "
            "Expert). Your scope is calibration and discrimination only.\n\n"
            "If the item is psychometrically clean, return an empty list — but "
            "examine first.\n\n"
            f"--- ITEM (id={item.id}) ---\n{item.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections

    def audit_exam(self, draft: ExamDraft, exam_spec: ExamSpec) -> ExamAudit:
        """Exam-level audit: produce ExamReport plus any exam-level objections.

        Exam-level objections have target='exam_global'. Examples: 'difficulty
        curve is 80% easy when target was 30% easy', 'CLO X is uncovered by
        the accepted items even though the blueprint allocated it'.
        """
        prompt = (
            "Audit the draft exam as a whole. Produce two things in your response: "
            "an ExamReport (Bloom distribution, difficulty curve actual vs. target, "
            "CLO coverage with item counts and points, imbalance notes, and a one-"
            "paragraph summary), and a list of exam-level objections.\n\n"
            "Exam-level objections use target='exam_global'. Raise them when the "
            "draft as a whole violates the exam spec or the blueprint in a way no "
            "per-item edit can fix: difficulty distribution drift, Bloom drift "
            "(item-level Bloom levels shift the actual distribution away from the "
            "blueprint), uncovered CLOs despite blueprint allocation, point-total "
            "mismatch, time-budget mismatch.\n\n"
            "Difficulty curve: compute easy/medium/hard counts from item "
            "difficulty_est fields, and include the targets from "
            "exam_spec.difficulty_distribution.\n\n"
            "Bloom distribution: aggregate from item bloom_level, counting items "
            "and summing points per level.\n\n"
            "CLO coverage: for each CLO in the implicit course spec referenced by "
            "items' clo_refs, count items and points and set is_covered = "
            "(item_count > 0).\n\n"
            f"--- EXAM SPEC ---\n{exam_spec.model_dump_json(indent=2)}\n\n"
            f"--- EXAM DRAFT ---\n{draft.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, ExamAudit)
