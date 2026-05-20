"""Learning Outcomes Alignment (LOA) agent.

Verifies constructive alignment (Biggs): each item must provide evidence for
the CLO it claims to assess, at the cognitive level that CLO promises. Gates
items in Phase 2 (verify_alignment), produces objections in Phase 3 (critique),
and proposes fixes when asked (suggest_realignment).
"""

from typing import ClassVar

from models import (
    CLO,
    AlignmentResult,
    Item,
    ItemDraft,
    ObjectionDraft,
    ObjectionDraftList,
    RealignmentSuggestion,
)

from agents.base import BaseAgent


def _format_clos(clos: list[CLO]) -> str:
    return "\n".join(
        f"- id={c.id} | bloom={c.bloom_level.value} | "
        f"knowledge={c.knowledge_type.value}\n  {c.text}"
        for c in clos
    )


def _format_item(item: ItemDraft | Item) -> str:
    return item.model_dump_json(indent=2)


class LearningOutcomesAlignmentAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "learning_outcomes_alignment"

    def verify_alignment(
        self,
        item: ItemDraft,
        clos: list[CLO],
    ) -> AlignmentResult:
        """Phase 2 gate: does this item match its claimed CLO refs and Bloom level?

        Accepts ItemDraft (so Phase 2 pre-promotion items work); Item is a
        subclass and is accepted naturally.
        """
        prompt = (
            "Verify the constructive alignment of this item. Determine three things:\n"
            "  1. The actual cognitive level the item demands of a student (judged by "
            "what the student must do to answer, not by the verb in the stem).\n"
            "  2. The CLOs the item actually provides evidence for, citing them by id.\n"
            "  3. Whether this matches the item's claimed bloom_level and clo_refs.\n\n"
            "Set is_aligned=true ONLY when the actual Bloom level matches the claimed "
            "bloom_level AND the actual CLOs include at least one of the claimed "
            "clo_refs. Otherwise set is_aligned=false and explain in notes what is "
            "off and by how much.\n\n"
            f"--- COURSE LEARNING OUTCOMES ---\n{_format_clos(clos)}\n\n"
            f"--- ITEM ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, AlignmentResult)

    def critique(
        self,
        item: Item,
        clos: list[CLO],
    ) -> list[ObjectionDraft]:
        """Phase 3 critique: produce alignment-only objections for one item."""
        prompt = (
            f"Inspect item {item.id} for alignment defects and return a list of "
            "objections. Use category strings from the catalogue in your constitution. "
            "Set target to the item id exactly as given. One objection per distinct "
            "alignment defect; cite specific CLOs by id in the claim.\n\n"
            "Examine carefully before returning an empty list. If the item is properly "
            "aligned, return an empty list — but do not return an empty list as a "
            "default. Examine first.\n\n"
            "Stay in your lane: do not raise objections about content correctness "
            "(SME), mechanics (IWS), readability (Accessibility), or difficulty "
            "(Psychometrician).\n\n"
            f"--- COURSE LEARNING OUTCOMES ---\n{_format_clos(clos)}\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections

    def suggest_realignment(
        self,
        item: Item,
        clos: list[CLO],
    ) -> RealignmentSuggestion:
        """Recommend a fix for a misaligned item.

        Called by the Moderator after a critique-rebuttal cycle or when verify_
        alignment has flagged an item that the trade-off policy says should be
        rescued rather than dropped.
        """
        prompt = (
            "This item is misaligned. Diagnose the misalignment and recommend ONE "
            "action:\n"
            "  - edit_item: the item can be edited to match its claimed clo_refs and "
            "bloom_level. Provide edit_summary describing what the SME should change.\n"
            "  - remap_clos: the item is well-formed but evidences different CLOs "
            "than claimed. Provide proposed_clo_refs.\n"
            "  - remap_bloom: the item is well-formed but operates at a different "
            "Bloom level than claimed. Provide proposed_bloom_level.\n"
            "  - reject: the item cannot reasonably be aligned to any course CLO at "
            "any defensible Bloom level. Explain in rationale.\n\n"
            "Provide a clear diagnosis and a rationale for your recommendation, "
            "citing CLOs by id.\n\n"
            f"--- COURSE LEARNING OUTCOMES ---\n{_format_clos(clos)}\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, RealignmentSuggestion)
