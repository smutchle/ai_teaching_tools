"""Subject Matter Expert agent.

Responsible for content fidelity: theme extraction, item proposal grounded in
uploaded materials, and responding to critique with edits or rebuttals.
"""

import math
from pathlib import Path
from typing import ClassVar

from anthropic import Anthropic

from events import EventLog
from models import (
    BlueprintCell,
    Chunk,
    EditResult,
    Item,
    ItemDraft,
    ItemDraftList,
    Objection,
    Rebuttal,
    Theme,
    ThemeList,
)
from retrieval import Retriever

from agents.base import BaseAgent


def _format_chunks(chunks: list[Chunk]) -> str:
    parts: list[str] = []
    for c in chunks:
        loc = f" | {c.locator}" if c.locator else ""
        tag = " [PRIOR EXAM — style only]" if c.is_prior_exam else ""
        parts.append(
            f"[chunk_id={c.id} | source={c.source_doc}{loc}{tag}]\n{c.text}"
        )
    return "\n\n".join(parts)


class SMEAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "sme"

    def __init__(
        self,
        persona_dir: Path,
        model: str,
        client: Anthropic,
        retriever: Retriever,
        *,
        event_log: EventLog | None = None,
        max_tokens: int = 4096,
    ) -> None:
        super().__init__(
            persona_dir,
            model,
            client,
            event_log=event_log,
            max_tokens=max_tokens,
        )
        self._retriever: Retriever = retriever

    def gather_context(self, query: str, *, k: int = 8) -> list[Chunk]:
        """Look up chunks for a query.

        Callers pre-retrieve and pass chunks to the verbs as the default flow;
        use this method for follow-up retrieval when an initial chunk set is
        too sparse to support the requested item count for a blueprint cell.
        """
        return self._retriever.search(query, k=k)

    def propose_themes(
        self,
        materials: list[Chunk],
        target_count: int,
    ) -> list[Theme]:
        prompt = (
            f"Extract up to {target_count} candidate themes from the course materials "
            "below. Rank them by centrality to the course (rank 1 = most central). "
            "Each theme must cite at least one source chunk. A theme is a coherent "
            "idea, technique, or framework that an exam item could be built around — "
            "not a single fact and not a whole chapter.\n\n"
            f"--- COURSE MATERIALS ---\n{_format_chunks(materials)}"
        )
        return self._invoke(prompt, ThemeList).themes

    def propose_items(
        self,
        cell: BlueprintCell,
        context: list[Chunk],
        *,
        overgenerate_factor: float = 1.5,
    ) -> list[ItemDraft]:
        n = max(1, math.ceil(cell.target_item_count * overgenerate_factor))
        prompt = (
            f"Propose {n} candidate exam items for the blueprint cell below. The "
            "Moderator will downselect to the target count after critique; aim for "
            "variety in stem framing while staying within the cell's topic and "
            "cognitive level.\n\n"
            "--- BLUEPRINT CELL ---\n"
            f"topic: {cell.topic_name} (id={cell.topic_id})\n"
            f"bloom_level: {cell.bloom_level.value}\n"
            f"target_item_count: {cell.target_item_count}\n"
            f"target_points: {cell.target_points}\n"
            f"clo_refs: {cell.clo_refs}\n\n"
            f"--- SOURCE CONTEXT ---\n{_format_chunks(context)}\n\n"
            "Every item must cite at least one source_ref drawn from the chunks "
            "above; use chunk_id values exactly as given. Chunks tagged "
            "[PRIOR EXAM — style only] may inform tone and difficulty but must "
            "never be cited as a source_ref for new item content."
        )
        return self._invoke(prompt, ItemDraftList).items

    def edit_item(self, item: Item, objection: Objection) -> EditResult:
        prompt = (
            "Edit the item below to address the objection. Produce a clean revised "
            "draft and a short rationale explaining what you changed and why. "
            "Address the underlying concern, not just the surface symptom. If the "
            "objection cannot be addressed without compromising content fidelity, "
            "use rebut_objection instead of this verb.\n\n"
            f"--- ITEM (id={item.id}) ---\n{item.model_dump_json(indent=2)}\n\n"
            f"--- OBJECTION ---\n{objection.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, EditResult)

    def rebut_objection(self, item: Item, objection: Objection) -> Rebuttal:
        prompt = (
            "Respond to the objection against this item. Choose one stance:\n"
            "  - accept: the objection has merit; you will edit. Provide a brief "
            "proposed_edit_summary; the actual edit will be requested via "
            "edit_item next.\n"
            "  - rebut: the objection conflicts with content fidelity or technical "
            "correctness on defensible grounds. State the technical argument.\n"
            "  - defer: more information is needed. Name what would let you decide.\n\n"
            "Empty agreement is worse than a clean disagreement the Moderator can "
            "resolve via the trade-off policy. Do not perform collegiality.\n\n"
            f"--- ITEM (id={item.id}) ---\n{item.model_dump_json(indent=2)}\n\n"
            f"--- OBJECTION ---\n{objection.model_dump_json(indent=2)}"
        )
        return self._invoke(prompt, Rebuttal)
