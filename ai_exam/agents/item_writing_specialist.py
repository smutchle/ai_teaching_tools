"""Item-Writing Specialist agent.

Catches mechanical flaws in items: grammatical cueing, distractor quality,
stem clarity, cue leakage, and other defects from the item-writing literature
(Haladyna et al.). Does not touch what the item is testing — that is the SME's
domain. Does not verify cognitive alignment — that is the LOA agent's domain.
"""

from typing import ClassVar

from models import (
    EditResult,
    Item,
    ItemDraft,
    Objection,
    ObjectionDraft,
    ObjectionDraftList,
)

from agents.base import BaseAgent


def _format_item(item: ItemDraft | Item) -> str:
    return item.model_dump_json(indent=2)


class ItemWritingSpecialistAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "item_writing_specialist"

    def cleanup(self, item: ItemDraft) -> EditResult:
        """Phase 2 mechanical cleanup of a freshly-proposed item.

        Pre-promotion: input has no id yet. Address mechanics only — do not
        change the construct being measured, the cognitive level, the source
        citations, or the point value.
        """
        prompt = (
            "Review this freshly-proposed item for mechanical flaws and produce a "
            "cleaned-up draft. Address only mechanics: grammatical cueing, distractor "
            "quality and plausibility, stem clarity, option formatting consistency, "
            "absolute-language giveaways, length tells, double negatives, ambiguous "
            "phrasing, missing rubric language, and similar. Do NOT change what the "
            "item is testing, its cognitive level, its point value, its citations, or "
            "its CLO/topic references — those belong to the SME and the LOA agent. If "
            "you find no mechanical issues, return the item unchanged with a rationale "
            "of 'no mechanical issues found'.\n\n"
            f"--- ITEM DRAFT ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, EditResult)

    def critique(self, item: Item) -> list[ObjectionDraft]:
        """Phase 3 critique: produce mechanics-only objections for one item.

        Empty critique is suspicious and will be re-prompted by the Moderator if
        the anti-sycophancy minimum is in force. Examine carefully before
        returning an empty list.
        """
        prompt = (
            f"Inspect item {item.id} for mechanical flaws and return a list of "
            "objections. One objection per distinct flaw. Cite the specific element "
            "(e.g., 'option B', 'stem', 'rubric criterion 2'). Use category strings "
            "from the catalogue in your constitution; if a flaw genuinely does not fit "
            "any catalogued category, use a precise new kebab-case category and explain "
            "in the claim.\n\n"
            "Set target to the item id exactly as given. Set severity based on the "
            "consequence to score validity: critical = the flaw makes the item "
            "unscorable or trivially exploitable; high = the flaw substantially biases "
            "scoring; medium = the flaw is a real but bounded measurement defect; "
            "low = the flaw is a polish issue.\n\n"
            "If you find no mechanical flaws after careful inspection, return an empty "
            "objections list — but do not return an empty list as a default. Examine "
            "first.\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections

    def propose_edit(self, item: Item, objection: Objection) -> EditResult:
        """Edit an item to address a mechanical objection.

        The Moderator routes mechanical objections here in preference to the SME.
        If the objection cannot be addressed without changing the construct being
        measured, name that in the rationale and produce the smallest mechanical
        edit you can defend.
        """
        prompt = (
            "Edit this item to address the objection. Produce a clean revised draft "
            "and a short rationale explaining what you changed and why. Address "
            "mechanics only; do not change the construct being measured, the cognitive "
            "level, the point value, or the citations. If the objection truly requires "
            "content changes outside your domain, say so in the rationale and produce "
            "the smallest mechanical edit you can defend — the Moderator will route to "
            "the SME for the content portion if needed.\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}\n\n"
            f"--- OBJECTION ---\n{objection.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, EditResult)
