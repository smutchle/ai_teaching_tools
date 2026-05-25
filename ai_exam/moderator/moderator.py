"""Deterministic orchestrator. Phase 0–3.

Phase 0 (intake) → Phase 1 (themes + blueprint + CP1) → Phase 2 (per-cell
propose + cleanup + verify + ground + CP2) → Phase 3 (epoch loop of critique
+ SME accept/rebut/defer + re-verify + convergence + CP3).

Promotes drafts to first-class records (ItemDraft → Item, ObjectionDraft →
Objection), writes phase / routing / checkpoint events to the EventLog, and
appends a ProvenanceEvent to each item on every state change. The trade-off
policy's max_epochs caps Phase 3; convergence (no critical/high objections
open at end of an epoch) lets it exit early.

Synchronous Phase 3 critique. True parallelism is an optimization that hides
bugs during debug; switch to async once the loop is stable.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agents import (
    AccessibilityExpertAgent,
    AdversarialStudentAgent,
    BlueprintArchitectAgent,
    GroundingVerifierAgent,
    ItemWritingSpecialistAgent,
    LearningOutcomesAlignmentAgent,
    PsychometricianAgent,
    SMEAgent,
)
from agents.base import AgentResponseError
from events import AgentEvent, EventKind, EventLog
from models import (
    AccommodationKind,
    AlignmentResult,
    Blueprint,
    Chunk,
    CourseSpec,
    Difficulty,
    EditResult,
    ExamAudit,
    ExamDraft,
    ExamSpec,
    GroundingResult,
    Item,
    ItemDraft,
    ItemObjections,
    ItemSlot,
    ItemStatus,
    ItemType,
    ItemVariant,
    Objection,
    ObjectionDraft,
    ObjectionSeverity,
    ProvenanceEvent,
    RealignmentSuggestion,
    Rebuttal,
    RebuttalStance,
    Theme,
)
from retrieval import Retriever

from parallel import gather_sync

from moderator.policy import TradeOffPolicy


@dataclass
class AgentRoster:
    """Bundle of the agents the Moderator needs across Phase 0–3."""

    sme: SMEAgent
    blueprint_architect: BlueprintArchitectAgent
    iws: ItemWritingSpecialistAgent
    loa: LearningOutcomesAlignmentAgent
    grounding: GroundingVerifierAgent
    accessibility: AccessibilityExpertAgent
    adversarial_student: AdversarialStudentAgent
    psychometrician: PsychometricianAgent

    def all_agents(self) -> tuple:
        return (
            self.sme, self.blueprint_architect, self.iws, self.loa,
            self.grounding, self.accessibility, self.adversarial_student,
            self.psychometrician,
        )


@dataclass
class EpochMetrics:
    """Summary of one Phase 3 epoch's activity."""

    epoch: int
    new_objections_by_severity: dict[str, int] = field(default_factory=dict)
    resolved_via_edit: int = 0
    rebutted: int = 0
    deferred: int = 0
    items_rejected: int = 0
    critical_high_open_at_end: int = 0
    converged: bool = False


@dataclass
class Phase2SlotOutcome:
    """One slot's Phase 2 result.

    A slot either lands one accepted Item (status DRAFT, with slot_id stamped)
    or stays unfilled. Up to ``slot_max_retries`` redraft attempts may run
    inside ``_phase_2_slot`` before giving up; each rejected candidate is
    returned in ``rejected`` for the snapshot.
    """

    slot: ItemSlot
    item: Item | None
    rejected: list[tuple[Item, str]]
    cited_chunks: list[Chunk]


_DIFFICULTY_ORDER: dict[Difficulty, int] = {
    Difficulty.EASY: 0,
    Difficulty.MEDIUM: 1,
    Difficulty.HARD: 2,
}


def _difficulty_distance(a: Difficulty, b: Difficulty) -> int:
    return abs(_DIFFICULTY_ORDER[a] - _DIFFICULTY_ORDER[b])


@dataclass
class Phase4Result:
    """End-of-pipeline bundle: exam audit + accommodation variants + exported files.

    Phase 4 is intentionally report-only: the audit's exam-level objections
    surface in the report but never trigger a remediation loop. Resolving
    them is the instructor's job at CP3.
    """

    audit: ExamAudit
    variants: list[ItemVariant] = field(default_factory=list)
    export_paths: list[Path] = field(default_factory=list)


@dataclass
class _RoutingOutcome:
    """Per-item result of the Phase 3 routing pass.

    Returned by `_route_item_objections` (one worker per item, run in parallel).
    The Moderator merges these into shared state on the main thread so no two
    threads ever mutate `draft.objections_open`/`metrics` concurrently. Item
    mutations (status, provenance, fields) are safe because each worker owns
    its own item.
    """

    item_id: str
    resolved_objection_ids: list[str] = field(default_factory=list)
    rebutted: int = 0
    deferred: int = 0
    item_rejected: bool = False


class Moderator:
    """Phase 0–2 state machine."""

    def __init__(
        self,
        *,
        course_spec: CourseSpec,
        exam_spec: ExamSpec,
        policy: TradeOffPolicy,
        retriever: Retriever,
        full_corpus: list[Chunk],
        agents: AgentRoster,
        event_log: EventLog,
        outputs_dir: Path,
        themes_target_count: int = 12,
        chunks_per_cell: int = 6,
    ) -> None:
        self._course_spec = course_spec
        self._exam_spec = exam_spec
        self._policy = policy
        self._retriever = retriever
        self._full_corpus = full_corpus
        self._agents = agents
        self._event_log = event_log
        self._outputs_dir = outputs_dir
        self._themes_target = themes_target_count
        self._chunks_per_cell = chunks_per_cell
        self._epoch = 0
        self._item_counter = 0
        # Phase 2 runs cells in parallel; the counter is the one piece of
        # cross-worker shared state. Lock it.
        self._item_counter_lock = threading.Lock()
        # Tracks how many Phase-3 redraft attempts each slot_id has already
        # consumed. Caps Phase-3 work for slots the materials simply can't
        # support and prevents an infinite redraft loop.
        self._slot_redraft_attempts: dict[str, int] = {}

        outputs_dir.mkdir(parents=True, exist_ok=True)
        self._stamp_epoch_on_agents()
        self._apply_notation_directive()

    def _stamp_epoch_on_agents(self) -> None:
        """Push the current epoch onto every agent so their invocation events
        bucket correctly in the chat transcript."""
        for ag in self._agents.all_agents():
            ag.set_epoch(self._epoch)

    def _apply_notation_directive(self) -> None:
        """Inject ExamSpec-derived notation rules into every agent's
        constitution at startup.

        Agents otherwise default to whatever style the persona authored,
        which produces a mix of LaTeX and raw Unicode across runs. Forcing
        a single convention from ExamSpec keeps stems, answers, rubrics,
        and critique text consistent — and matters for downstream Quarto
        rendering (raw Unicode special characters often misrender in PDF
        without explicit font support).
        """
        if self._exam_spec.latex_required:
            latex_rule = (
                "## Notation conventions (runtime — set by ExamSpec)\n\n"
                "Use LaTeX for ALL mathematical and discipline-specific "
                "notation, in stems, options, answers, rubrics, and critique "
                "text. Do NOT emit raw Unicode for symbols that have a LaTeX "
                "form:\n"
                "- Greek letters: `\\Delta` not Δ; `\\Omega` not Ω; "
                "`\\alpha\\beta\\gamma` not αβγ.\n"
                "- Arrows: `\\rightarrow` not →; `\\rightleftharpoons` not ⇌; "
                "`\\Rightarrow` not ⇒.\n"
                "- Accents and modifiers: `\\circ` not °; superscripts/"
                "subscripts via `^{...}` and `_{...}` not Unicode "
                "(`x^{-1}` not x⁻¹; `H_2O` not H₂O).\n"
                "- Approximations and inequalities: `\\approx` not ≈; "
                "`\\leq` not ≤; `\\geq` not ≥; `\\neq` not ≠.\n"
                "- Common operators: `\\pm` not ±; `\\times` not ×; "
                "`\\cdot` not ·.\n\n"
                "Wrap math in `$...$` (inline) or `$$...$$` (display). "
                "Text outside math regions should be plain ASCII."
            )
        else:
            latex_rule = (
                "## Notation conventions (runtime — set by ExamSpec)\n\n"
                "Do NOT use LaTeX. Use plain text for all mathematical "
                "content. Spell out Greek letters ('delta H', 'omega') "
                "rather than emitting `\\Delta` or Δ. Spell out operators "
                "('plus or minus', 'approximately equal to') rather than "
                "using `\\pm` or ±. Render units as readable text "
                "('joules per mole per kelvin', not 'J/mol·K' or "
                "'J mol^{-1} K^{-1}')."
            )

        if not self._exam_spec.figure_support:
            figure_rule = (
                "\n\n## Figures\n\n"
                "This exam delivery format does NOT support figures or "
                "images. Do not reference figures, diagrams, or images in "
                "any stem. All data the student needs must be embedded in "
                "the stem as text (e.g., a list of values, a small ASCII "
                "table, or a verbal description)."
            )
        else:
            figure_rule = ""

        directive = latex_rule + figure_rule
        for ag in self._agents.all_agents():
            ag.append_to_constitution(directive)

    # -- public entry point ------------------------------------------------------

    def run_through_checkpoint_2(self) -> ExamDraft:
        self._emit_phase("phase_0", "Intake: corpus ingested, agents wired, ready")

        # Phase 1
        self._emit_phase("phase_1", "Blueprinting: themes → blueprint → checkpoint 1")
        themes = self._extract_themes()
        self._snapshot("phase_1_themes.json", {"themes": [t.model_dump(mode="json") for t in themes]})

        blueprint = self._propose_blueprint(themes)
        self._snapshot("phase_1_blueprint.json", blueprint.model_dump(mode="json"))
        self._checkpoint(1, "blueprint approved [auto]")

        # Phase 2 — iterate the SlotPlan, not the cells. Each slot's
        # (item_type, difficulty) is pinned by the Blueprint Architect, so
        # one item per slot is produced and the global type+difficulty mix
        # cannot drift. Slots are independent; the only cross-slot shared
        # state is ``_item_counter``, locked inside ``_promote_for_slot``.
        #
        # Each slot is wrapped so a model-output failure in one slot doesn't
        # collapse the gather — the slot stays unfilled and Phase 4 audit
        # surfaces the gap.
        slot_plan = blueprint.slot_plan
        self._emit_phase(
            "phase_2",
            f"Item generation: per-slot propose → cleanup → verify → ground "
            f"({len(slot_plan.slots)} slots)",
        )

        outcomes = gather_sync([
            (lambda s=slot: self._safe_phase_2_slot(s, blueprint))
            for slot in slot_plan.slots
        ])

        all_accepted: list[Item] = []
        all_rejected: list[tuple[Item, str]] = []
        unfilled_slot_ids: list[str] = []
        for o in outcomes:
            if o.item is not None:
                all_accepted.append(o.item)
            else:
                unfilled_slot_ids.append(o.slot.slot_id)
            all_rejected.extend(o.rejected)

        self._snapshot(
            "phase_2_items.json",
            {
                "accepted": [it.model_dump(mode="json") for it in all_accepted],
                "rejected": [
                    {"item": it.model_dump(mode="json"), "reason": reason}
                    for it, reason in all_rejected
                ],
                "unfilled_slots": unfilled_slot_ids,
            },
        )
        self._checkpoint(
            2,
            f"item bank approved [auto]: {len(all_accepted)} accepted, "
            f"{len(all_rejected)} rejected, "
            f"{len(unfilled_slot_ids)} slots unfilled",
        )

        draft = ExamDraft(items=all_accepted, blueprint=blueprint, epoch=self._epoch)
        self._snapshot("exam_draft.json", draft.model_dump(mode="json"))
        return draft

    # -- Phase 1 -----------------------------------------------------------------

    def _extract_themes(self) -> list[Theme]:
        return self._agents.sme.propose_themes(self._full_corpus, target_count=self._themes_target)

    def _propose_blueprint(self, themes: list[Theme]) -> Blueprint:
        return self._agents.blueprint_architect.propose_blueprint(
            course_spec=self._course_spec,
            exam_spec=self._exam_spec,
            themes=themes,
        )

    # -- Phase 2 (slot-based) ---------------------------------------------------

    def _safe_phase_2_slot(
        self, slot: ItemSlot, blueprint: Blueprint,
    ) -> Phase2SlotOutcome:
        """Per-slot wrapper. Catches model-output failures and leaves the slot
        unfilled rather than collapsing the gather."""
        try:
            return self._phase_2_slot(slot, blueprint)
        except (ValidationError, AgentResponseError) as exc:
            self._emit_routing(
                target=f"slot:{slot.slot_id}",
                decision=(
                    f"slot failed and unfilled: "
                    f"{type(exc).__name__}: {str(exc)[:200]}"
                ),
            )
            return Phase2SlotOutcome(slot=slot, item=None, rejected=[], cited_chunks=[])

    # Per-slot redraft budget. SME gets one extra retry against the same slot
    # if every candidate from the first pass fails verification. Two is enough
    # in practice; more burns model time on slots the materials can't support.
    _SLOT_MAX_ATTEMPTS = 2

    def _phase_2_slot(
        self, slot: ItemSlot, blueprint: Blueprint,
    ) -> Phase2SlotOutcome:
        # Retrieve context for the slot using its topic name. Same K as the
        # legacy per-cell flow.
        ctx_chunks = self._retriever.search(slot.topic_name, k=self._chunks_per_cell)
        if not ctx_chunks:
            self._emit_routing(
                target=f"slot:{slot.slot_id}",
                decision="unfilled: no retrieval context for topic",
            )
            return Phase2SlotOutcome(slot=slot, item=None, rejected=[], cited_chunks=[])

        # Resolve cell ClOs (use the slot's clo_refs as the primary set, but
        # fall back to the parent cell's clo_refs if the slot is unset).
        clo_ref_set = set(slot.clo_refs)
        if not clo_ref_set:
            for cell in blueprint.cells:
                if cell.topic_id == slot.topic_id and cell.bloom_level == slot.bloom_level:
                    clo_ref_set = set(cell.clo_refs)
                    break
        slot_clos = [c for c in self._course_spec.clos if c.id in clo_ref_set]

        rejected: list[tuple[Item, str]] = []
        accepted: Item | None = None

        for attempt in range(1, self._SLOT_MAX_ATTEMPTS + 1):
            drafts = self._agents.sme.propose_items_for_slot(
                slot, ctx_chunks,
                clos=slot_clos,
                guiding_principles=self._course_spec.guiding_principles,
                overgenerate_factor=3.0,
            )

            for draft in drafts:
                # 1. Slot-contract pre-filter: type must match exactly,
                # difficulty within tolerance. Reject before promotion to
                # save IWS/LOA/Grounding calls.
                if draft.type != slot.item_type:
                    item = self._promote_for_slot(draft, slot, cleanup_rationale="")
                    reason = (
                        f"slot_contract: item_type={draft.type.value} "
                        f"does not match slot.item_type={slot.item_type.value}"
                    )
                    self._append_provenance(
                        item, agent="moderator", action="rejected", rationale=reason,
                    )
                    item.status = ItemStatus.REJECTED
                    rejected.append((item, reason))
                    continue
                if _difficulty_distance(draft.difficulty_est, slot.difficulty) > 1:
                    item = self._promote_for_slot(draft, slot, cleanup_rationale="")
                    reason = (
                        f"slot_contract: difficulty_est={draft.difficulty_est.value} "
                        f"too far from slot.difficulty={slot.difficulty.value}"
                    )
                    self._append_provenance(
                        item, agent="moderator", action="rejected", rationale=reason,
                    )
                    item.status = ItemStatus.REJECTED
                    rejected.append((item, reason))
                    continue

                # 2. IWS cleanup. Skip cleanly on malformed result.
                try:
                    cleanup_result: EditResult = self._agents.iws.cleanup(draft)
                    cleaned_draft = cleanup_result.updated_draft
                    cleanup_rationale = cleanup_result.rationale
                except (ValidationError, AgentResponseError) as exc:
                    cleaned_draft = draft
                    cleanup_rationale = (
                        f"skipped — IWS produced malformed EditResult "
                        f"({type(exc).__name__}); using SME draft as-is."
                    )

                # Re-check type after IWS cleanup — paranoid: IWS shouldn't
                # change type, but if it did the slot contract is violated.
                if cleaned_draft.type != slot.item_type:
                    cleaned_draft = cleaned_draft.model_copy(
                        update={"type": slot.item_type},
                    )

                # 3. Promote to Item, stamp slot_id and snap difficulty_est
                # to the slot's target so downstream readers see the
                # contracted difficulty (the SME's value is preserved in
                # provenance via the cleanup rationale).
                item = self._promote_for_slot(
                    cleaned_draft, slot, cleanup_rationale=cleanup_rationale,
                )
                if item.difficulty_est != slot.difficulty:
                    self._append_provenance(
                        item, agent="moderator", action="edited",
                        rationale=(
                            f"[slot_contract] snap difficulty_est: "
                            f"{item.difficulty_est.value} → {slot.difficulty.value}"
                        ),
                    )
                    item.difficulty_est = slot.difficulty

                # 4. LOA alignment.
                alignment: AlignmentResult = self._agents.loa.verify_alignment(
                    item, self._course_spec.clos,
                )
                if not alignment.is_aligned:
                    if not self._try_realignment(item):
                        reason = f"loa_misaligned: {alignment.notes}"
                        item.status = ItemStatus.REJECTED
                        rejected.append((item, reason))
                        continue
                else:
                    self._append_provenance(
                        item,
                        agent="learning_outcomes_alignment",
                        action="accepted",
                        rationale=(
                            f"aligned at {alignment.actual_bloom_level.value} "
                            f"for {alignment.actual_clo_refs}"
                        ),
                    )

                # 5. Look up cited chunks for grounding.
                cited: list[Chunk] = []
                missing_ids: list[str] = []
                for ref in item.source_refs:
                    try:
                        cited.append(self._retriever.get(ref.chunk_id))
                    except KeyError:
                        missing_ids.append(ref.chunk_id)
                if missing_ids:
                    reason = f"grounding_missing_chunks: {missing_ids}"
                    self._append_provenance(
                        item, agent="grounding_verifier", action="rejected", rationale=reason,
                    )
                    item.status = ItemStatus.REJECTED
                    rejected.append((item, reason))
                    continue

                # 6. Grounding.
                grounding: GroundingResult = self._agents.grounding.verify(item, cited)
                if not grounding.is_grounded:
                    reason = f"grounding_failed: {grounding.diagnosis}"
                    self._append_provenance(
                        item, agent="grounding_verifier", action="rejected", rationale=reason,
                    )
                    item.status = ItemStatus.REJECTED
                    rejected.append((item, reason))
                    continue
                self._append_provenance(
                    item, agent="grounding_verifier", action="accepted", rationale=grounding.diagnosis,
                )

                accepted = item
                break

            if accepted is not None:
                break

        self._emit_routing(
            target=f"slot:{slot.slot_id}",
            decision=(
                f"{'filled' if accepted is not None else 'unfilled'} "
                f"({len(rejected)} candidate(s) rejected across "
                f"{attempt} attempt(s))"
            ),
        )
        return Phase2SlotOutcome(
            slot=slot, item=accepted, rejected=rejected, cited_chunks=ctx_chunks,
        )

    def _promote_for_slot(
        self,
        draft: ItemDraft,
        slot: ItemSlot,
        *,
        cleanup_rationale: str,
    ) -> Item:
        """Slot-aware promotion. Stamps slot_id and uses slot.topic_id /
        slot.bloom_level for the SME provenance rationale.
        """
        with self._item_counter_lock:
            self._item_counter += 1
            item_id = f"item_{self._item_counter:04d}"
        item = Item(
            **draft.model_dump(),
            id=item_id,
            status=ItemStatus.DRAFT,
            slot_id=slot.slot_id,
            provenance=[],
        )
        item.provenance.append(self._provenance_event(
            agent="sme",
            action="proposed",
            target=item_id,
            rationale=(
                f"proposed for slot {slot.slot_id} "
                f"(type={slot.item_type.value} difficulty={slot.difficulty.value} "
                f"topic={slot.topic_id} bloom={slot.bloom_level.value})"
            ),
        ))
        if cleanup_rationale:
            item.provenance.append(self._provenance_event(
                agent="item_writing_specialist",
                action="edited",
                target=item_id,
                rationale=cleanup_rationale,
            ))
        return item

    def _try_realignment(self, item: Item) -> bool:
        """Ask LOA to suggest a realignment, apply it, re-verify.

        Phase-2 dominant failure pattern: SME labels an apply-level item as
        analyze. LOA already computed the actual Bloom level during
        verify_alignment, but the orchestrator was throwing that signal
        away. This recovers those items at the cost of one `suggest_
        realignment` call + one `verify_alignment` re-check.

        Returns True iff the item is now aligned (and item is patched
        in place). Returns False if LOA recommends `edit_item` or
        `reject`, or if the re-verify still fails after a remap. Provenance
        is appended in either path so the audit trail is complete.
        """
        suggestion: RealignmentSuggestion = self._agents.loa.suggest_realignment(
            item, self._course_spec.clos,
        )
        action = suggestion.suggested_action

        if action == "remap_bloom" and suggestion.proposed_bloom_level is not None:
            old = item.bloom_level.value
            item.bloom_level = suggestion.proposed_bloom_level
            self._append_provenance(
                item, agent="learning_outcomes_alignment", action="edited",
                rationale=(
                    f"[realignment] remap_bloom: {old} → "
                    f"{item.bloom_level.value}. {suggestion.rationale}"
                ),
            )
        elif action == "remap_clos" and suggestion.proposed_clo_refs:
            old = list(item.clo_refs)
            item.clo_refs = suggestion.proposed_clo_refs
            self._append_provenance(
                item, agent="learning_outcomes_alignment", action="edited",
                rationale=(
                    f"[realignment] remap_clos: {old} → "
                    f"{item.clo_refs}. {suggestion.rationale}"
                ),
            )
        else:
            # 'edit_item' or 'reject' or missing proposed_* — can't auto-
            # recover in Phase 2 (no SME edit loop here). Drop with a
            # rationale that names LOA's verdict.
            self._append_provenance(
                item, agent="learning_outcomes_alignment", action="rejected",
                rationale=(
                    f"[realignment] action={action}; not auto-recoverable. "
                    f"{suggestion.rationale[:200]}"
                ),
            )
            return False

        # Re-verify after the patch.
        recheck = self._agents.loa.verify_alignment(item, self._course_spec.clos)
        if recheck.is_aligned:
            self._append_provenance(
                item, agent="learning_outcomes_alignment", action="accepted",
                rationale=(
                    f"[post-realignment] aligned at "
                    f"{recheck.actual_bloom_level.value} for "
                    f"{recheck.actual_clo_refs}"
                ),
            )
            return True
        self._append_provenance(
            item, agent="learning_outcomes_alignment", action="rejected",
            rationale=f"[post-realignment] still misaligned: {recheck.notes}",
        )
        return False

    # -- promotion + provenance --------------------------------------------------

    def _append_provenance(
        self,
        item: Item,
        *,
        agent: str,
        action: str,
        rationale: str,
        diff: str | None = None,
    ) -> None:
        item.provenance.append(
            self._provenance_event(agent=agent, action=action, target=item.id, rationale=rationale, diff=diff)
        )

    def _provenance_event(
        self,
        *,
        agent: str,
        action: str,
        target: str,
        rationale: str,
        diff: str | None = None,
    ) -> ProvenanceEvent:
        return ProvenanceEvent(
            epoch=self._epoch,
            agent=agent,
            action=action,  # type: ignore[arg-type]
            target=target,
            diff=diff,
            rationale=rationale,
            timestamp=datetime.now(timezone.utc),
        )

    # -- event log + snapshots ---------------------------------------------------

    def _emit_phase(self, phase: str, message: str) -> None:
        self._event_log.append(AgentEvent(
            timestamp=datetime.now(timezone.utc),
            epoch=self._epoch,
            agent="moderator",
            kind=EventKind.ROUTING_DECISION,
            extras={"phase": phase, "message": message},
        ))

    def _emit_routing(self, *, target: str, decision: str) -> None:
        self._event_log.append(AgentEvent(
            timestamp=datetime.now(timezone.utc),
            epoch=self._epoch,
            agent="moderator",
            kind=EventKind.ROUTING_DECISION,
            target=target,
            extras={"decision": decision},
        ))

    def _checkpoint(self, num: int, message: str) -> None:
        self._event_log.append(AgentEvent(
            timestamp=datetime.now(timezone.utc),
            epoch=self._epoch,
            agent="moderator",
            kind=EventKind.CHECKPOINT_REACHED,
            extras={"checkpoint": num, "message": message},
        ))

    def _snapshot(self, filename: str, payload: object) -> None:
        import json
        path = self._outputs_dir / filename
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # =================================================================
    #  Phase 3 — refinement epochs
    # =================================================================

    def run_through_checkpoint_3(
        self,
        draft: ExamDraft,
        *,
        max_epochs_override: int | None = None,
    ) -> ExamDraft:
        """Run the refinement epoch loop on a Phase-2 draft until convergence
        or max_epochs. Returns the same draft with refined items, accumulated
        objections, and ProvenanceEvents stamped on each touched item."""
        max_epochs = max_epochs_override if max_epochs_override is not None else self._policy.max_epochs
        self._emit_phase(
            "phase_3",
            f"Refinement: critic epochs (max={max_epochs}); convergence rule = {self._policy.convergence_rule}",
        )

        all_metrics: list[EpochMetrics] = []
        for epoch_num in range(1, max_epochs + 1):
            self._epoch = epoch_num
            self._stamp_epoch_on_agents()

            metrics = self._run_epoch(draft, epoch_num)
            # Redraft any slot that lost its item this epoch (or earlier).
            # Newly-drafted items will enter the *next* epoch's critique pass.
            self._phase_3_redraft_dropped_slots(draft, epoch_num)
            all_metrics.append(metrics)
            self._snapshot(
                f"phase_3_epoch_{epoch_num}.json",
                {
                    "metrics": {
                        "epoch": metrics.epoch,
                        "new_objections_by_severity": metrics.new_objections_by_severity,
                        "resolved_via_edit": metrics.resolved_via_edit,
                        "rebutted": metrics.rebutted,
                        "deferred": metrics.deferred,
                        "items_rejected": metrics.items_rejected,
                        "critical_high_open_at_end": metrics.critical_high_open_at_end,
                        "converged": metrics.converged,
                    },
                    "draft_after_epoch": draft.model_dump(mode="json"),
                },
            )
            self._emit_routing(
                target=f"epoch_{epoch_num}",
                decision=(
                    f"end of epoch {epoch_num}: "
                    f"new={metrics.new_objections_by_severity}, "
                    f"resolved={metrics.resolved_via_edit}, "
                    f"rebutted={metrics.rebutted}, "
                    f"deferred={metrics.deferred}, "
                    f"items_rejected={metrics.items_rejected}, "
                    f"crit_high_open={metrics.critical_high_open_at_end}"
                ),
            )
            if metrics.converged:
                self._emit_routing(
                    target=f"epoch_{epoch_num}",
                    decision="CONVERGED: no critical or high objections open",
                )
                break

        # Mark surviving items as REFINED
        for item in draft.items:
            if item.status == ItemStatus.DRAFT:
                item.status = ItemStatus.REFINED
        draft.epoch = self._epoch

        self._snapshot("phase_3_final_draft.json", draft.model_dump(mode="json"))
        survivors = [i for i in draft.items if i.status != ItemStatus.REJECTED]
        self._checkpoint(
            3,
            f"phase 3 complete after {self._epoch} epoch(s); "
            f"{len(survivors)} items survive ({sum(i.points for i in survivors)} pts); "
            f"{len(draft.objections_open)} objections still open, "
            f"{len(draft.objections_resolved)} resolved",
        )
        return draft

    # =================================================================
    #  Phase 4 — finalize: audit + variants + export
    # =================================================================

    def run_phase_4(self, draft: ExamDraft) -> Phase4Result:
        """Audit the exam, generate accommodation variants, and write the export
        bundle. Audit objections are reported, never auto-remediated."""
        self._emit_phase("phase_4", "Finalize: exam audit → variants → export")

        # 1. Exam-level audit (report-only — no remediation loop).
        audit: ExamAudit = self._agents.psychometrician.audit_exam(draft, self._exam_spec)
        # 1b. Deterministic mix-invariant check on top of the Psychometrician's
        # audit. The Psychometrician inspects difficulty curve etc., but we
        # also need a strict count-based check against ExamSpec so any
        # type-mix or difficulty-mix drift surfaces as a critical objection
        # in the final report rather than silently shipping.
        mix_objections = self._mix_invariant_objections(draft)
        if mix_objections:
            audit = audit.model_copy(update={
                "objections": list(audit.objections) + mix_objections,
            })
        self._snapshot("phase_4_audit.json", audit.model_dump(mode="json"))
        self._emit_routing(
            target="exam_global",
            decision=(
                f"audit: {len(audit.objections)} exam-level objections raised "
                f"({len(mix_objections)} from mix-invariant check); "
                f"{len(audit.report.imbalance_notes)} imbalance notes"
            ),
        )

        # 2. Variants: every surviving item × every required accommodation.
        # Persona returns "no change" variants when applicable; we keep them
        # so each (item, kind) pair has a defensible record.
        survivors = [i for i in draft.items if i.status != ItemStatus.REJECTED]
        kinds: list[AccommodationKind] = list(self._exam_spec.accommodations_required)
        variants: list[ItemVariant] = []
        if survivors and kinds:
            pairs: list[tuple[Item, AccommodationKind]] = [
                (item, kind) for item in survivors for kind in kinds
            ]
            calls = [
                (lambda it=item, k=kind: self._agents.accessibility.generate_variant(it, k))
                for item, kind in pairs
            ]
            variants = gather_sync(calls)
            self._snapshot(
                "phase_4_variants.json",
                {"variants": [v.model_dump(mode="json") for v in variants]},
            )
        self._emit_routing(
            target="variants",
            decision=(
                f"generated {len(variants)} variants "
                f"({len(survivors)} items × {len(kinds)} accommodations)"
            ),
        )

        # 3. Export bundle (Quarto-rendered to PDF / DOCX / LaTeX + provenance).
        # Lazy import: keep the export toolchain dependency local to Phase 4 so
        # earlier phases never touch quarto/subprocess.
        from export import build_export_bundle  # noqa: WPS433
        bundle = build_export_bundle(
            draft=draft,
            audit=audit,
            variants=variants,
            exam_spec=self._exam_spec,
            course_spec=self._course_spec,
            outputs_dir=self._outputs_dir,
        )
        self._emit_routing(
            target="export_bundle",
            decision=(
                f"wrote {len(bundle.produced)} files to {bundle.bundle_dir.name}/; "
                f"render failures: {sum(len(v) for v in bundle.failures.values())}"
            ),
        )
        self._checkpoint(
            3,  # Phase 4's gating checkpoint is still CP3 (final review).
            f"phase 4 complete: bundle at {bundle.bundle_dir}",
        )
        return Phase4Result(audit=audit, variants=variants, export_paths=bundle.produced)

    def _mix_invariant_objections(self, draft: ExamDraft) -> list[ObjectionDraft]:
        """Deterministic Phase-4 invariant check: surviving items must match
        ``ExamSpec.item_type_counts`` and ``ExamSpec.difficulty_distribution``
        exactly. Any deviation becomes a critical exam-level objection.

        This is a final safety net — if Phase 2 slot enforcement and Phase 3
        redrafting both did their job, this returns an empty list. When they
        couldn't (e.g. the materials genuinely don't support a slot), the
        instructor sees the precise gap in the audit report rather than
        getting a wrong-mix exam.
        """
        survivors = [it for it in draft.items if it.status != ItemStatus.REJECTED]

        # Type histogram
        actual_types: dict[ItemType, int] = {t: 0 for t in ItemType}
        for it in survivors:
            actual_types[it.type] = actual_types.get(it.type, 0) + 1
        target_types = self._exam_spec.target_item_type_counts()

        # Difficulty histogram
        actual_diffs: dict[Difficulty, int] = {d: 0 for d in Difficulty}
        for it in survivors:
            actual_diffs[it.difficulty_est] = actual_diffs.get(it.difficulty_est, 0) + 1
        target_diffs = self._exam_spec.target_difficulty_counts()

        objections: list[ObjectionDraft] = []
        for t, want in target_types.items():
            got = actual_types.get(t, 0)
            if got != want:
                objections.append(ObjectionDraft(
                    severity=ObjectionSeverity.CRITICAL,
                    category="mix_invariant",
                    target="exam_global",
                    claim=(
                        f"Item-type mix violates ExamSpec: "
                        f"{t.value} got {got}, expected {want}."
                    ),
                    suggested_fix=(
                        f"Re-run Phase 2 for slots of type {t.value}, or amend "
                        f"the ExamSpec to match what the materials support."
                    ),
                ))
        for d, want in target_diffs.items():
            got = actual_diffs.get(d, 0)
            if got != want:
                objections.append(ObjectionDraft(
                    severity=ObjectionSeverity.CRITICAL,
                    category="mix_invariant",
                    target="exam_global",
                    claim=(
                        f"Difficulty mix violates ExamSpec: "
                        f"{d.value} got {got}, expected {want}."
                    ),
                    suggested_fix=(
                        f"Inspect slots flagged at the wrong difficulty during "
                        f"Phase 2, or amend the ExamSpec ratios."
                    ),
                ))
        return objections

    def _run_epoch(self, draft: ExamDraft, epoch_num: int) -> EpochMetrics:
        metrics = EpochMetrics(epoch=epoch_num)

        # --- 1. Critique pass: one batched call per critic over all items ---
        # Collapses 3 critics × N items into just 3 calls (1 per critic), each
        # of which returns objections for every item. The three critics still
        # run in parallel via gather_sync.
        survivors = [i for i in draft.items if i.status != ItemStatus.REJECTED]
        critics: list[tuple[str, object]] = [
            ("accessibility", self._agents.accessibility),
            ("adversarial_student", self._agents.adversarial_student),
            ("psychometrician", self._agents.psychometrician),
        ]
        if survivors:
            batched_results: list[list[ItemObjections]] = gather_sync([
                (lambda a=agent: a.critique_batch(survivors))  # type: ignore[attr-defined]
                for _, agent in critics
            ])
            survivors_by_id: dict[str, Item] = {it.id: it for it in survivors}
            for (critic_name, _), entries in zip(critics, batched_results):
                for entry in entries:
                    item = survivors_by_id.get(entry.item_id)
                    if item is None:
                        # Critic emitted an item_id we didn't ask about — skip
                        # rather than crash. Could happen if the model
                        # hallucinated an id; the schema can't prevent it.
                        continue
                    for od in entry.objections:
                        obj = self._promote_objection(od, agent_name=critic_name)
                        draft.objections_open.append(obj)
                        metrics.new_objections_by_severity[obj.severity.value] = (
                            metrics.new_objections_by_severity.get(obj.severity.value, 0) + 1
                        )
                        self._append_provenance(
                            item,
                            agent=critic_name,
                            action="objected",
                            rationale=f"[{obj.severity.value}/{obj.category}] {obj.claim}",
                        )

        # --- 2. Routing pass: process objections, parallelized across items ---
        # Within an item the objections must stay sequential (each edit mutates
        # the item). Across items the work is independent. Group by target
        # item.id, dispatch one worker per item, then merge outcomes on the
        # main thread.
        by_item: dict[str, list[Objection]] = {}
        for objection in list(draft.objections_open):
            by_item.setdefault(objection.target, []).append(objection)

        routable: list[tuple[Item, list[Objection]]] = []
        for target_id, objs in by_item.items():
            item = self._find_item(draft, target_id)
            if item is None or item.status == ItemStatus.REJECTED:
                # Item gone or already rejected — objections moot.
                continue
            routable.append((item, objs))

        outcomes: list[_RoutingOutcome] = gather_sync([
            (lambda it=item, os=objs: self._safe_route_item_objections(draft, it, os))
            for item, objs in routable
        ])

        # Merge outcomes back into shared state (single-threaded).
        resolved_ids: set[str] = set()
        for out in outcomes:
            resolved_ids.update(out.resolved_objection_ids)
            metrics.resolved_via_edit += len(out.resolved_objection_ids)
            metrics.rebutted += out.rebutted
            metrics.deferred += out.deferred
            if out.item_rejected:
                metrics.items_rejected += 1
        # Move resolved objections in one pass to avoid O(N²) list.remove churn.
        if resolved_ids:
            still_open: list[Objection] = []
            for o in draft.objections_open:
                if o.id in resolved_ids:
                    draft.objections_resolved.append(o)
                else:
                    still_open.append(o)
            draft.objections_open = still_open

        # --- 3. Convergence: count critical/high still open at end of epoch ---
        metrics.critical_high_open_at_end = sum(
            1 for o in draft.objections_open
            if o.severity in (ObjectionSeverity.CRITICAL, ObjectionSeverity.HIGH)
        )
        metrics.converged = (metrics.critical_high_open_at_end == 0)
        return metrics

    # -- Phase 3 helpers --------------------------------------------------------

    # Cap on Phase-3 redraft attempts per slot. One redraft is the typical
    # case (an item rejected in epoch N comes back in epoch N+1); the second
    # attempt covers the rare case where the first redraft also fails LOA
    # or grounding. Beyond two we accept the slot stays unfilled and let
    # the Phase 4 audit surface it.
    _PHASE_3_REDRAFT_CAP_PER_SLOT = 2

    def _phase_3_redraft_dropped_slots(self, draft: ExamDraft, epoch_num: int) -> None:
        """Refill slots whose item was rejected this epoch (or earlier).

        Iterates ``draft.blueprint.slot_plan`` and, for any slot with no
        accepted/refined Item attached, calls ``_phase_2_slot`` to produce
        a replacement. Per-slot redraft count is capped so the loop is
        bounded even when the materials can't support the slot.
        """
        slot_plan = draft.blueprint.slot_plan
        if not slot_plan.slots:
            return

        live_slot_ids: set[str] = {
            it.slot_id for it in draft.items
            if it.slot_id is not None and it.status != ItemStatus.REJECTED
        }

        redraftable: list[ItemSlot] = []
        for slot in slot_plan.slots:
            if slot.slot_id in live_slot_ids:
                continue
            if self._slot_redraft_attempts.get(slot.slot_id, 0) >= self._PHASE_3_REDRAFT_CAP_PER_SLOT:
                continue
            redraftable.append(slot)

        if not redraftable:
            return

        self._emit_routing(
            target=f"epoch_{epoch_num}",
            decision=f"redraft pass: {len(redraftable)} dropped slot(s) to refill",
        )

        outcomes = gather_sync([
            (lambda s=slot: self._safe_phase_2_slot(s, draft.blueprint))
            for slot in redraftable
        ])

        for outcome in outcomes:
            self._slot_redraft_attempts[outcome.slot.slot_id] = (
                self._slot_redraft_attempts.get(outcome.slot.slot_id, 0) + 1
            )
            if outcome.item is None:
                continue
            # Replace the rejected item in place (if any) so item ordering
            # stays roughly stable; otherwise append.
            replaced = False
            for idx, existing in enumerate(draft.items):
                if existing.slot_id == outcome.slot.slot_id and existing.status == ItemStatus.REJECTED:
                    draft.items[idx] = outcome.item
                    replaced = True
                    break
            if not replaced:
                draft.items.append(outcome.item)
            self._append_provenance(
                outcome.item,
                agent="moderator",
                action="proposed",
                rationale=(
                    f"[phase_3_redraft] slot {outcome.slot.slot_id} refilled "
                    f"after epoch {epoch_num} drop"
                ),
            )

    def _safe_route_item_objections(
        self,
        draft: ExamDraft,
        item: Item,
        objections: list[Objection],
    ) -> _RoutingOutcome:
        """Per-item routing wrapper. Catches the specific model-output
        failure types and marks the item rejected with a routing-event
        rationale, so one bad item can't collapse the whole epoch's gather.
        Mirrors _safe_phase_2_slot for symmetry."""
        try:
            return self._route_item_objections(draft, item, objections)
        except (ValidationError, AgentResponseError) as exc:
            item.status = ItemStatus.REJECTED
            self._append_provenance(
                item, agent="moderator", action="rejected",
                rationale=(
                    f"routing aborted: {type(exc).__name__}: "
                    f"{str(exc)[:200]}"
                ),
            )
            self._emit_routing(
                target=item.id,
                decision=f"item routing failed and item rejected: {type(exc).__name__}",
            )
            return _RoutingOutcome(item_id=item.id, item_rejected=True)

    def _route_item_objections(
        self,
        draft: ExamDraft,
        item: Item,
        objections: list[Objection],
    ) -> _RoutingOutcome:
        """Process one item's objections in a worker thread.

        Pass order:
          1. Critical objections force a SME edit (sequential — each edit
             may mutate the item).
          2. Non-critical objections get stances from ONE batched SME call,
             then are processed in order: ACCEPT → edit + reverify;
             REBUT/DEFER → log provenance, objection stays open.

        Batching the non-critical stance decisions also lets SME consider
        overlapping objections together rather than rebutting each blind to
        the others — both a cost win (1 Opus/Kimi call vs N) and a quality
        win for conflict resolution.

        The worker returns a `_RoutingOutcome` value; the Moderator merges
        that into `draft.objections_open`/`metrics` on the main thread.
        """
        out = _RoutingOutcome(item_id=item.id)

        critical = [
            o for o in objections if o.severity == ObjectionSeverity.CRITICAL
        ]
        non_critical = [
            o for o in objections if o.severity != ObjectionSeverity.CRITICAL
        ]

        # --- 1. Critical objections: force edit, sequentially ---
        for objection in critical:
            if item.status == ItemStatus.REJECTED:
                break
            result = self._sme_edit_and_reverify(
                draft, item, objection, reason="critical_forced",
            )
            if result == "accepted":
                out.resolved_objection_ids.append(objection.id)
            else:
                out.item_rejected = True

        if item.status == ItemStatus.REJECTED or not non_critical:
            return out

        # --- 2. Non-critical objections: one batched stance call, then dispatch ---
        rebuttals: list[Rebuttal] = self._agents.sme.rebut_objections(
            item, non_critical,
        )
        rebuttal_by_id: dict[str, Rebuttal] = {r.objection_id: r for r in rebuttals}

        for objection in non_critical:
            if item.status == ItemStatus.REJECTED:
                break
            rebuttal = rebuttal_by_id.get(objection.id)
            if rebuttal is None:
                # rebut_objections guarantees mapping; defensive skip.
                continue
            if rebuttal.stance == RebuttalStance.ACCEPT:
                result = self._sme_edit_and_reverify(
                    draft, item, objection,
                    reason=f"sme_accept: {rebuttal.rationale}",
                )
                if result == "accepted":
                    out.resolved_objection_ids.append(objection.id)
                else:
                    out.item_rejected = True
            elif rebuttal.stance == RebuttalStance.REBUT:
                self._append_provenance(
                    item, agent="sme", action="resolved",
                    rationale=f"REBUT objection {objection.id}: {rebuttal.rationale}",
                )
                out.rebutted += 1
            else:  # DEFER
                self._append_provenance(
                    item, agent="sme", action="resolved",
                    rationale=f"DEFER objection {objection.id}: {rebuttal.rationale}",
                )
                out.deferred += 1
        return out

    def _sme_edit_and_reverify(
        self,
        draft: ExamDraft,
        item: Item,
        objection: Objection,
        *,
        reason: str,
    ) -> str:
        """SME edits → IWS cleanup → LOA verify → Grounding verify.
        Mutates the Item in place. Returns 'accepted' or 'rejected'."""
        edit_result: EditResult = self._agents.sme.edit_item(item, objection)
        self._replace_item_fields(item, edit_result.updated_draft)
        self._append_provenance(
            item, agent="sme", action="edited",
            rationale=f"[{reason}] {edit_result.rationale}",
        )

        # Mechanical re-cleanup after the content edit. If IWS returns a
        # malformed EditResult (e.g. missing updated_draft), skip the
        # cleanup and proceed with the SME-edited item unchanged — the
        # cleanup is a polish step, not load-bearing.
        try:
            cleanup: EditResult = self._agents.iws.cleanup(item)
            self._replace_item_fields(item, cleanup.updated_draft)
            self._append_provenance(
                item, agent="item_writing_specialist", action="edited",
                rationale=f"[post-edit cleanup] {cleanup.rationale}",
            )
        except (ValidationError, AgentResponseError) as exc:
            self._append_provenance(
                item, agent="item_writing_specialist", action="edited",
                rationale=(
                    f"[post-edit cleanup] skipped — IWS produced malformed "
                    f"EditResult ({type(exc).__name__}); using SME edit as-is."
                ),
            )

        # Slot-contract guard: SME edits + IWS cleanup must not change the
        # item's type or difficulty away from what the slot pinned at
        # planning time. Both moves are silently rolled back here, with a
        # provenance note so the audit trail shows the drift attempt.
        self._enforce_slot_contract(item, draft.blueprint)

        # LOA re-check.
        alignment: AlignmentResult = self._agents.loa.verify_alignment(item, self._course_spec.clos)
        if not alignment.is_aligned:
            item.status = ItemStatus.REJECTED
            self._append_provenance(
                item, agent="learning_outcomes_alignment", action="rejected",
                rationale=f"[post-edit] loa_misaligned: {alignment.notes}",
            )
            return "rejected"

        # Grounding re-check (look up cited chunks, then verify).
        cited: list[Chunk] = []
        missing: list[str] = []
        for ref in item.source_refs:
            try:
                cited.append(self._retriever.get(ref.chunk_id))
            except KeyError:
                missing.append(ref.chunk_id)
        if missing:
            item.status = ItemStatus.REJECTED
            self._append_provenance(
                item, agent="grounding_verifier", action="rejected",
                rationale=f"[post-edit] grounding_missing_chunks: {missing}",
            )
            return "rejected"

        grounding: GroundingResult = self._agents.grounding.verify(item, cited)
        if not grounding.is_grounded:
            item.status = ItemStatus.REJECTED
            self._append_provenance(
                item, agent="grounding_verifier", action="rejected",
                rationale=f"[post-edit] grounding_failed: {grounding.diagnosis}",
            )
            return "rejected"

        self._append_provenance(
            item, agent="grounding_verifier", action="accepted",
            rationale=f"[post-edit] {grounding.diagnosis}",
        )
        return "accepted"

    def _promote_objection(self, draft_obj: ObjectionDraft, *, agent_name: str) -> Objection:
        return Objection(
            id=f"obj_{uuid.uuid4().hex[:8]}",
            agent=agent_name,
            severity=draft_obj.severity,
            category=draft_obj.category,
            target=draft_obj.target,
            claim=draft_obj.claim,
            suggested_fix=draft_obj.suggested_fix,
        )

    def _move_to_resolved(self, draft: ExamDraft, objection: Objection) -> None:
        if objection in draft.objections_open:
            draft.objections_open.remove(objection)
            draft.objections_resolved.append(objection)

    def _find_item(self, draft: ExamDraft, item_id: str) -> Item | None:
        for it in draft.items:
            if it.id == item_id:
                return it
        return None

    def _replace_item_fields(self, target: Item, source: ItemDraft) -> None:
        """Copy every ItemDraft field from source onto target, preserving the
        Item-only fields (id, status, slot_id, discrimination_est, provenance)."""
        for field_name in ItemDraft.model_fields:
            setattr(target, field_name, getattr(source, field_name))

    def _enforce_slot_contract(self, item: Item, blueprint: Blueprint) -> None:
        """Snap an item's type and difficulty back to the slot's pinned values
        after any post-Phase-2 edit.

        Phase-3 edits (SME edit_item, IWS cleanup) routinely move
        difficulty_est by one step ("we softened the stem so this is medium
        now") and occasionally even switch type. Either drift breaks the
        slot contract enforced in Phase 2 and shows up as a critical
        mix-invariant objection in Phase 4. The fix is mechanical: look up
        the slot, snap both fields, and write provenance so the change is
        visible. Item content is otherwise untouched.
        """
        if item.slot_id is None:
            return
        slot = next(
            (s for s in blueprint.slot_plan.slots if s.slot_id == item.slot_id),
            None,
        )
        if slot is None:
            return
        if item.type != slot.item_type:
            old = item.type.value
            item.type = slot.item_type
            self._append_provenance(
                item, agent="moderator", action="edited",
                rationale=(
                    f"[slot_contract] snap item_type: {old} → "
                    f"{item.type.value} (slot {slot.slot_id})"
                ),
            )
        if item.difficulty_est != slot.difficulty:
            old = item.difficulty_est.value
            item.difficulty_est = slot.difficulty
            self._append_provenance(
                item, agent="moderator", action="edited",
                rationale=(
                    f"[slot_contract] snap difficulty_est: {old} → "
                    f"{item.difficulty_est.value} (slot {slot.slot_id})"
                ),
            )
