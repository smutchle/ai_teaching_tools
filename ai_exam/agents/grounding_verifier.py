"""Grounding Verifier agent.

Lightweight Sonnet call that checks an SME-proposed item's answer is supported
by the chunks the SME cited. Not a full critic in the workflow sense (does not
participate in the epoch loop), but implemented as a BaseAgent subclass for
uniform event logging and structured output.
"""

from typing import ClassVar

from models import Chunk, GroundingResult, ItemDraft

from agents.base import BaseAgent


def _format_cited_chunks(chunks: list[Chunk]) -> str:
    if not chunks:
        return "(no chunks cited)"
    return "\n\n".join(
        f"[chunk_id={c.id} | source={c.source_doc} | {c.locator or 'no locator'}]\n{c.text}"
        for c in chunks
    )


class GroundingVerifierAgent(BaseAgent):
    PERSONA_NAME: ClassVar[str] = "grounding_verifier"

    def verify(self, item: ItemDraft, cited_chunks: list[Chunk]) -> GroundingResult:
        """Verify that the item's answer is supported by the cited chunks.

        The cited_chunks list should be exactly the chunks named in
        item.source_refs, looked up via the retriever's get(). If a chunk_id
        in source_refs is missing from the corpus, the caller should treat
        that as an automatic grounding failure rather than calling verify().
        """
        prompt = (
            "Verify that this item's answer is supported by the chunks below — and "
            "only by these chunks. Do not bring in outside knowledge.\n\n"
            "Decision rule: is_grounded = true ONLY when every claim in the "
            "answer_key is directly supported by at least one cited chunk, either "
            "stated explicitly or reachable by a single defensible deduction "
            "(substituting into a formula given in the chunks, applying a definition "
            "given in the chunks, reading a tabulated value). Otherwise is_grounded "
            "= false; itemize the gaps in missing_evidence.\n\n"
            f"--- ITEM ---\n{item.model_dump_json(indent=2)}\n\n"
            f"--- CITED CHUNKS ({len(cited_chunks)}) ---\n{_format_cited_chunks(cited_chunks)}"
        )
        return self._invoke(prompt, GroundingResult)
