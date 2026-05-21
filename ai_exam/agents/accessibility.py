"""Accessibility Expert agent.

Reviews items for universal design and construct-irrelevant difficulty;
generates accommodation variants (extended-time, screen-reader, large-print)
during finalization.
"""

from typing import ClassVar

from models import (
    AccommodationKind,
    EditResult,
    Item,
    ItemDraft,
    ItemObjections,
    ItemObjectionsBatch,
    ItemVariant,
    Objection,
    ObjectionDraft,
    ObjectionDraftList,
)

from agents.base import BaseAgent
from agents.critique_batch import normalize_critique_batch as _normalize_critique_batch


def _format_item(item: ItemDraft | Item) -> str:
    return item.model_dump_json(indent=2)


class AccessibilityExpertAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "accessibility"

    _CRITIQUE_GUIDANCE = (
        "Inspect each item for accessibility and universal-design issues. "
        "Use category strings from the catalogue in your constitution. One "
        "objection per distinct issue.\n\n"
        "Focus on construct-irrelevant difficulty: text that is harder than "
        "the construct it is meant to measure. Idioms, unnecessarily complex "
        "syntax, low-frequency vocabulary that is not the discipline's "
        "technical vocabulary, dense parenthetical asides, and culturally "
        "narrow examples all add load without measuring anything.\n\n"
        "Stay in your lane: do not raise objections about content correctness "
        "(SME), mechanics (IWS), alignment (LOA), or psychometric calibration "
        "(Psychometrician). When in doubt about whether a difficulty is "
        "construct-relevant, defer to the SME by raising a low-severity "
        "objection rather than a high-severity one."
    )

    def critique(self, item: Item) -> list[ObjectionDraft]:
        prompt = (
            f"{self._CRITIQUE_GUIDANCE}\n\nSet `target` to the item id exactly "
            f"as given. If the item is genuinely clean from an accessibility "
            f"standpoint, return an empty list — but examine first.\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, ObjectionDraftList).objections

    def critique_batch(self, items: list[Item]) -> list[ItemObjections]:
        """Batch form: critique all items in one call.

        Returns one ItemObjections entry per input item in input order. Empty
        objection lists are kept (so the caller sees explicit "no concerns"
        per item). Any objection whose `target` doesn't match its containing
        item is patched on the way out — the schema requires per-entry
        attribution and the Moderator relies on it.
        """
        if not items:
            return []
        item_blocks = [
            f"--- ITEM (id={it.id}) ---\n{_format_item(it)}" for it in items
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

    def propose_edit(self, item: Item, objection: Objection) -> EditResult:
        prompt = (
            "Edit this item to address the accessibility objection. Make the smallest "
            "change that resolves the construct-irrelevant difficulty without "
            "changing what the item measures, its cognitive level, its point value, "
            "or its citations. If the fix requires changing the construct, say so in "
            "the rationale and produce the smallest edit you can defend; the "
            "Moderator will route to the SME if needed.\n\n"
            "Preserve technical vocabulary that is part of the discipline. The goal "
            "is to remove load that is not the construct, not to dumb the item down.\n\n"
            f"--- ITEM (id={item.id}) ---\n{_format_item(item)}\n\n"
            f"--- OBJECTION ---\n{objection.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, EditResult)

    def generate_variant(self, item: Item, kind: AccommodationKind) -> ItemVariant:
        """Produce an accommodation-specific variant of the item.

        The base item is not modified. The variant is a separate ItemDraft with
        adaptation_notes describing what changed and why.
        """
        kind_guidance = {
            AccommodationKind.EXTENDED_TIME: (
                "Extended time accommodations typically do not change the item text. "
                "Verify that nothing in the stem assumes a time constraint (e.g., 'In "
                "the next 30 seconds, ...'). If the item text is unchanged, return it "
                "as-is and explain in adaptation_notes."
            ),
            AccommodationKind.SCREEN_READER: (
                "Screen-reader variants must: replace figures with descriptive alt "
                "text in the stem when the figure carries construct-relevant "
                "information; spell out abbreviations on first use; replace tables "
                "with linear prose where the layout itself is not the construct; "
                "and convert LaTeX expressions to a screen-reader-friendly form "
                "(e.g., 'k sub a' for k_a) or include an inline natural-language "
                "reading alongside the LaTeX."
            ),
            AccommodationKind.LARGE_PRINT: (
                "Large-print variants typically do not change the item text but may "
                "require reformatting: break long stems into shorter paragraphs, "
                "convert dense option lists into vertically-stacked options, and "
                "ensure figures render cleanly at 18pt. Note any reformatting in "
                "adaptation_notes."
            ),
        }[kind]
        prompt = (
            f"Produce a {kind.value} variant of this item. Set kind = {kind.value!r} "
            f"and base_item_id = {item.id!r}.\n\n"
            f"Guidance for this accommodation:\n{kind_guidance}\n\n"
            "The variant must preserve the construct being measured, the cognitive "
            "level, the answer key, and the point value. Document changes in "
            "adaptation_notes. If no changes are needed, return the item as-is and "
            "say so in adaptation_notes.\n\n"
            f"--- BASE ITEM (id={item.id}) ---\n{_format_item(item)}"
        )
        return self._invoke(prompt, ItemVariant)
