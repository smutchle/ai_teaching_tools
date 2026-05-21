"""Subject Matter Expert agent.

Responsible for content fidelity: theme extraction, item proposal grounded in
uploaded materials, and responding to critique with edits or rebuttals.
"""

import math
from pathlib import Path
from typing import ClassVar

from events import EventLog
from models import (
    CLO,
    BlueprintCell,
    Chunk,
    EditResult,
    Item,
    ItemDraft,
    ItemDraftList,
    Objection,
    Rebuttal,
    RebuttalBatch,
    Theme,
    ThemeList,
)
from parallel import gather_sync
from providers import LLMProvider
from retrieval import Retriever

from agents.base import BaseAgent


# Per-chunk overhead in characters when formatting for the prompt. Covers the
# "[chunk_id=... | source=...]" header line plus the blank-line separator.
_CHUNK_FRAME_CHARS = 120

# Safe per-call budget for chunk content sent to Claude. Each agent call also
# carries the constitution (~5–10k tokens) and prompt scaffolding, plus an
# output buffer. 320k chars ≈ 80k tokens — leaves clean headroom under the
# 200k-token context window and keeps attention sharp on the chunks rather
# than smearing it across a full-window prompt.
_DEFAULT_THEME_CHUNK_BUDGET_CHARS = 320_000


def _format_chunks(chunks: list[Chunk]) -> str:
    parts: list[str] = []
    for c in chunks:
        loc = f" | {c.locator}" if c.locator else ""
        tag = " [PRIOR EXAM — style only]" if c.is_prior_exam else ""
        parts.append(
            f"[chunk_id={c.id} | source={c.source_doc}{loc}{tag}]\n{c.text}"
        )
    return "\n\n".join(parts)


def _estimate_chunk_chars(c: Chunk) -> int:
    """Estimate of the formatted-prompt char cost of a chunk.

    Mirrors `_format_chunks`'s framing — the header line, the chunk text, and
    the trailing blank line — without actually rendering it. Used for budget
    accounting in `_split_chunks_by_budget`.
    """
    return len(c.text) + _CHUNK_FRAME_CHARS


def _split_chunks_by_budget(
    chunks: list[Chunk],
    budget_chars: int,
) -> list[list[Chunk]]:
    """Greedy bin-pack chunks into batches that each stay under `budget_chars`.

    Preserves chunk order so each batch tells a locally-coherent story
    (lecture order, chapter order, etc.). A single chunk that exceeds budget
    on its own becomes its own batch — Claude handles it as best it can; we
    don't split chunks mid-content.
    """
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_chars = 0
    for c in chunks:
        cost = _estimate_chunk_chars(c)
        if current and current_chars + cost > budget_chars:
            batches.append(current)
            current = [c]
            current_chars = cost
        else:
            current.append(c)
            current_chars += cost
    if current:
        batches.append(current)
    return batches


class SMEAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "sme"

    def __init__(
        self,
        persona_dir: Path,
        provider: LLMProvider,
        retriever: Retriever,
        *,
        event_log: EventLog | None = None,
        max_tokens: int = 4096,
        theme_chunk_budget_chars: int = _DEFAULT_THEME_CHUNK_BUDGET_CHARS,
    ) -> None:
        super().__init__(
            persona_dir,
            provider,
            event_log=event_log,
            max_tokens=max_tokens,
        )
        self._retriever: Retriever = retriever
        # Above this many chars of formatted chunk content, propose_themes
        # splits the corpus into batches, extracts themes per batch in
        # parallel, and consolidates the results with one final SME call.
        self._theme_chunk_budget_chars: int = theme_chunk_budget_chars

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
        """Extract `target_count` themes from the corpus.

        Small corpora (under `_theme_chunk_budget_chars` of formatted content)
        go through the single-call path with no behavior change. Larger corpora
        are split into budget-sized batches; each batch is theme-extracted in
        parallel; the resulting per-batch theme lists are consolidated by one
        final SME call that merges duplicates and re-ranks globally.

        Sizing is by formatted-prompt char count, not raw chunk char count —
        the per-chunk header overhead matters for very small chunks and is
        included via `_estimate_chunk_chars`.
        """
        total_chars = sum(_estimate_chunk_chars(c) for c in materials)
        if total_chars <= self._theme_chunk_budget_chars:
            return self._propose_themes_single(materials, target_count)

        batches = _split_chunks_by_budget(materials, self._theme_chunk_budget_chars)
        # Each batch is independent — fan out and consolidate.
        per_batch: list[list[Theme]] = gather_sync([
            (lambda b=batch: self._propose_themes_single(b, target_count))
            for batch in batches
        ])
        return self._consolidate_themes(per_batch, target_count)

    def _propose_themes_single(
        self,
        materials: list[Chunk],
        target_count: int,
    ) -> list[Theme]:
        """One-shot theme extraction over a batch that fits in budget."""
        prompt = (
            f"Extract up to {target_count} candidate themes from the course materials "
            "below. Rank them by centrality to the course (rank 1 = most central). "
            "Each theme must cite at least one source chunk. A theme is a coherent "
            "idea, technique, or framework that an exam item could be built around — "
            "not a single fact and not a whole chapter.\n\n"
            f"--- COURSE MATERIALS ---\n{_format_chunks(materials)}"
        )
        return self._invoke(prompt, ThemeList).themes

    def _consolidate_themes(
        self,
        per_batch_themes: list[list[Theme]],
        target_count: int,
    ) -> list[Theme]:
        """Merge themes from multiple batch passes into one ranked list.

        The corpus was too large to fit in a single prompt, so it was split
        into batches and each batch produced its own ranked theme list.
        Different batches will surface near-duplicate themes (the same idea
        phrased slightly differently) and globally-comparable rankings are
        impossible from the per-batch views alone. This call asks the SME to
        do both jobs at once.
        """
        sections: list[str] = []
        for i, themes in enumerate(per_batch_themes, start=1):
            lines = [f"--- BATCH {i} THEMES ---"]
            for t in themes:
                refs = ", ".join(r.chunk_id for r in t.source_refs)
                lines.append(
                    f"rank={t.rank} id={t.id}\n"
                    f"  text: {t.text}\n"
                    f"  source chunks: {refs or '(none)'}"
                )
            sections.append("\n".join(lines))

        prompt = (
            f"You ran theme extraction on {len(per_batch_themes)} batches of a "
            f"corpus that was too large to fit in a single pass. Each batch "
            f"produced its own ranked theme list. Consolidate them into the top "
            f"{target_count} themes for the corpus as a whole.\n\n"
            "Rules:\n"
            "  - Merge themes that are the same idea phrased differently. When "
            "merging, take the union of their source_refs.\n"
            "  - Preserve themes that are genuinely distinct ideas.\n"
            "  - Re-rank globally by centrality to the course (rank 1 = most "
            "central). Per-batch ranks are local and not globally comparable; "
            "use them only as one signal among many.\n"
            "  - Every kept theme must keep at least one source_ref from its "
            "original batch(es).\n\n"
            + "\n\n".join(sections)
        )
        return self._invoke(prompt, ThemeList).themes

    def propose_items(
        self,
        cell: BlueprintCell,
        context: list[Chunk],
        *,
        clos: list[CLO] | None = None,
        guiding_principles: str = "",
        overgenerate_factor: float = 2.5,
    ) -> list[ItemDraft]:
        """Draft items for a blueprint cell.

        `clos` is the list of resolved CLO objects for the cell's `clo_refs`
        — passed by the Moderator so the SME can read what each CLO actually
        *means* rather than guessing from an opaque id like `clo_hess`.
        Misalignment with the claimed CLO is the dominant Phase-2 rejection
        cause; giving SME the CLO text up-front cuts that pattern at the
        source.

        `guiding_principles` is the free-text instructor preamble from
        `CourseSpec.guiding_principles` (e.g. "emphasize quantitative
        reasoning, no rote definitions"). Shapes stem-framing voice.
        """
        n = max(1, math.ceil(cell.target_item_count * overgenerate_factor))

        clo_section = ""
        if clos:
            clo_lines = [
                f"- **{c.id}** (bloom={c.bloom_level.value}, "
                f"knowledge={c.knowledge_type.value}): {c.text}"
                for c in clos
            ]
            clo_section = (
                "--- LEARNING OUTCOMES THE ITEM MUST EVIDENCE ---\n"
                "Each item must produce evidence for at least one of these "
                "outcomes at the cell's Bloom level. The Bloom level on the "
                "CLO is what students must demonstrate — match it; do not "
                "label an apply-level item as analyze.\n"
                + "\n".join(clo_lines)
                + "\n\n"
            )

        principles_section = ""
        if guiding_principles.strip():
            principles_section = (
                "--- INSTRUCTOR'S GUIDING PRINCIPLES ---\n"
                f"{guiding_principles.strip()}\n\n"
            )

        prompt = (
            f"Propose {n} candidate exam items for the blueprint cell below. The "
            "Moderator will downselect to the target count after critique; aim for "
            "variety in stem framing while staying within the cell's topic and "
            "cognitive level.\n\n"
            + principles_section
            + clo_section
            + "--- BLUEPRINT CELL ---\n"
            f"topic: {cell.topic_name} (id={cell.topic_id})\n"
            f"bloom_level: {cell.bloom_level.value}\n"
            f"target_item_count: {cell.target_item_count}\n"
            f"target_points: {cell.target_points}\n"
            f"clo_refs (cite at least one in each item's clo_refs): {cell.clo_refs}\n\n"
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
        """Legacy single-objection rebut. The Moderator calls
        `rebut_objections` instead; kept for ad-hoc callers and tests."""
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

    def rebut_objections(
        self, item: Item, objections: list[Objection],
    ) -> list[Rebuttal]:
        """Batch form: one stance per objection in a single LLM call.

        Returns rebuttals in the same order as the input objections. If the
        model omits an objection_id we fall back to positional matching and
        patch the id back so downstream lookups by id work.
        """
        if not objections:
            return []
        prompt_parts: list[str] = [
            f"Respond to ALL {len(objections)} objections raised against this item. "
            "Produce exactly one Rebuttal per objection — match each by setting "
            "`objection_id` to the id given in the OBJECTIONS section. Choose one "
            "stance per objection:\n"
            "  - accept: the objection has merit; you will edit. Provide a brief "
            "proposed_edit_summary.\n"
            "  - rebut: the objection conflicts with content fidelity or technical "
            "correctness on defensible grounds. State the technical argument.\n"
            "  - defer: more information is needed. Name what would let you decide.\n\n"
            "Consider the objections together — a fix that resolves one may make "
            "another moot, or two critics may make conflicting demands. Decide "
            "stances holistically. Empty agreement is worse than a clean "
            "disagreement the Moderator can resolve. Do not perform collegiality.\n\n"
            f"--- ITEM (id={item.id}) ---\n{item.model_dump_json(indent=2)}\n\n"
            f"--- OBJECTIONS ({len(objections)}) ---\n",
        ]
        for obj in objections:
            prompt_parts.append("\n" + obj.model_dump_json(indent=2) + "\n")
        batch = self._invoke("".join(prompt_parts), RebuttalBatch)

        by_id: dict[str, Rebuttal] = {r.objection_id: r for r in batch.rebuttals}
        out: list[Rebuttal] = []
        for i, obj in enumerate(objections):
            r = by_id.get(obj.id)
            if r is None:
                # Positional fallback — patch the id so the Moderator's
                # id-keyed dispatch still works downstream.
                if i < len(batch.rebuttals):
                    r = batch.rebuttals[i].model_copy(update={"objection_id": obj.id})
                else:
                    raise RuntimeError(
                        f"SME.rebut_objections: model returned "
                        f"{len(batch.rebuttals)} rebuttals for "
                        f"{len(objections)} objections; "
                        f"objection {obj.id} has no rebuttal."
                    )
            out.append(r)
        return out
