"""Shared post-processing for batched critic responses.

All three critic agents (Accessibility, Adversarial Student, Psychometrician)
expose a `critique_batch(items)` that returns one `ItemObjections` entry per
input item. The model is asked to attribute each objection to its item via
`item_id` plus per-objection `target`. In practice models sometimes forget
to copy ids or include a target — we patch those defensively here so the
Moderator can rely on `out[i].item_id == items[i].id` and
`obj.target == items[i].id`.
"""

from __future__ import annotations

from models import Item, ItemObjections, ItemObjectionsBatch


def normalize_critique_batch(
    batch: ItemObjectionsBatch,
    items: list[Item],
) -> list[ItemObjections]:
    """Return one ItemObjections per input item, in input order.

    - Look up by `item_id` first.
    - If the model omitted an item, fall back to positional matching.
    - If still nothing, emit an empty entry rather than crashing.
    - Patch `target` on each contained ObjectionDraft to the matched item id
      so downstream item resolution by `objection.target` always works.
    """
    by_id: dict[str, ItemObjections] = {entry.item_id: entry for entry in batch.items}
    out: list[ItemObjections] = []
    for i, item in enumerate(items):
        entry = by_id.get(item.id)
        if entry is None and i < len(batch.items):
            # Positional fallback — patch the item_id so future lookups work.
            entry = batch.items[i].model_copy(update={"item_id": item.id})
        if entry is None:
            entry = ItemObjections(item_id=item.id, objections=[])
        # Patch per-objection target. Pydantic v2 allows attribute mutation by
        # default; we use model_copy to be explicit and avoid surprises.
        patched_objections = [
            (o if o.target == item.id else o.model_copy(update={"target": item.id}))
            for o in entry.objections
        ]
        out.append(entry.model_copy(update={"objections": patched_objections}))
    return out
