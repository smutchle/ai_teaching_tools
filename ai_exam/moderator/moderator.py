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

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
from events import AgentEvent, EventKind, EventLog
from models import (
    AlignmentResult,
    Blueprint,
    BlueprintCell,
    Chunk,
    CourseSpec,
    EditResult,
    ExamDraft,
    ExamSpec,
    GroundingResult,
    Item,
    ItemDraft,
    ItemStatus,
    Objection,
    ObjectionDraft,
    ObjectionSeverity,
    ProvenanceEvent,
    Rebuttal,
    RebuttalStance,
    Theme,
)
from retrieval import Retriever

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
class Phase2Outcome:
    """One blueprint cell's Phase 2 result."""

    cell: BlueprintCell
    accepted: list[Item]
    rejected: list[tuple[Item, str]]  # (item, reason)
    cited_chunks_by_item: dict[str, list[Chunk]]


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

        outputs_dir.mkdir(parents=True, exist_ok=True)
        self._stamp_epoch_on_agents()

    def _stamp_epoch_on_agents(self) -> None:
        """Push the current epoch onto every agent so their invocation events
        bucket correctly in the chat transcript."""
        for ag in self._agents.all_agents():
            ag.set_epoch(self._epoch)

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

        # Phase 2
        self._emit_phase("phase_2", "Item generation: per-cell propose → cleanup → verify → ground")
        outcomes = [self._phase_2_cell(cell) for cell in blueprint.cells]

        all_accepted: list[Item] = []
        all_rejected: list[tuple[Item, str]] = []
        for o in outcomes:
            all_accepted.extend(o.accepted)
            all_rejected.extend(o.rejected)

        self._snapshot(
            "phase_2_items.json",
            {
                "accepted": [it.model_dump(mode="json") for it in all_accepted],
                "rejected": [
                    {"item": it.model_dump(mode="json"), "reason": reason}
                    for it, reason in all_rejected
                ],
            },
        )
        self._checkpoint(2, f"item bank approved [auto]: {len(all_accepted)} accepted, {len(all_rejected)} rejected")

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

    # -- Phase 2 -----------------------------------------------------------------

    def _phase_2_cell(self, cell: BlueprintCell) -> Phase2Outcome:
        # Skip cells the blueprint marked as zero-item — BA sometimes produces
        # degenerate cells when it folds a topic's points into cross-citations
        # on other cells. Don't waste an SME call on them.
        if cell.target_item_count <= 0:
            self._emit_routing(
                target=f"cell:{cell.topic_id}:{cell.bloom_level.value}",
                decision="skipped: target_item_count=0",
            )
            return Phase2Outcome(cell=cell, accepted=[], rejected=[], cited_chunks_by_item={})

        # Retrieve context for this cell using the topic name as query.
        ctx_chunks = self._retriever.search(cell.topic_name, k=self._chunks_per_cell)
        if not ctx_chunks:
            return Phase2Outcome(cell=cell, accepted=[], rejected=[], cited_chunks_by_item={})

        # SME proposes 1.5× target item count.
        drafts = self._agents.sme.propose_items(cell, ctx_chunks, overgenerate_factor=1.5)

        accepted: list[Item] = []
        rejected: list[tuple[Item, str]] = []
        cited_chunks_by_item: dict[str, list[Chunk]] = {}

        for draft in drafts:
            # Stop if the cell is already full (check at top so the cell never
            # over-accepts, even when overgenerate gives us extra drafts).
            if len(accepted) >= cell.target_item_count:
                break
            # 1. IWS mechanical cleanup pre-promotion (still ItemDraft).
            cleanup_result: EditResult = self._agents.iws.cleanup(draft)
            cleaned_draft = cleanup_result.updated_draft

            # 2. Promote to Item with provenance for proposal + cleanup.
            item = self._promote(cleaned_draft, target_cell=cell, cleanup_rationale=cleanup_result.rationale)

            # 3. LOA alignment verification.
            alignment: AlignmentResult = self._agents.loa.verify_alignment(item, self._course_spec.clos)
            if not alignment.is_aligned:
                reason = f"loa_misaligned: {alignment.notes}"
                self._append_provenance(item, agent="learning_outcomes_alignment", action="rejected", rationale=reason)
                item.status = ItemStatus.REJECTED
                rejected.append((item, reason))
                continue
            self._append_provenance(
                item,
                agent="learning_outcomes_alignment",
                action="accepted",
                rationale=f"aligned at {alignment.actual_bloom_level.value} for {alignment.actual_clo_refs}",
            )

            # 4. Look up cited chunks for grounding; missing chunk_id is an automatic fail.
            cited: list[Chunk] = []
            missing_ids: list[str] = []
            for ref in item.source_refs:
                try:
                    cited.append(self._retriever.get(ref.chunk_id))
                except KeyError:
                    missing_ids.append(ref.chunk_id)
            if missing_ids:
                reason = f"grounding_missing_chunks: {missing_ids}"
                self._append_provenance(item, agent="grounding_verifier", action="rejected", rationale=reason)
                item.status = ItemStatus.REJECTED
                rejected.append((item, reason))
                continue
            cited_chunks_by_item[item.id] = cited

            # 5. Grounding Verifier.
            grounding: GroundingResult = self._agents.grounding.verify(item, cited)
            if not grounding.is_grounded:
                reason = f"grounding_failed: {grounding.diagnosis}"
                self._append_provenance(item, agent="grounding_verifier", action="rejected", rationale=reason)
                item.status = ItemStatus.REJECTED
                rejected.append((item, reason))
                continue
            self._append_provenance(item, agent="grounding_verifier", action="accepted", rationale=grounding.diagnosis)

            # 6. Item passes all gates; accept it. Loop-top check stops us
            #    from over-accepting next iteration.
            accepted.append(item)

        self._emit_routing(
            target=f"cell:{cell.topic_id}:{cell.bloom_level.value}",
            decision=f"{len(accepted)}/{cell.target_item_count} accepted, {len(rejected)} rejected",
        )
        return Phase2Outcome(cell=cell, accepted=accepted, rejected=rejected, cited_chunks_by_item=cited_chunks_by_item)

    # -- promotion + provenance --------------------------------------------------

    def _promote(
        self,
        draft: ItemDraft,
        *,
        target_cell: BlueprintCell,
        cleanup_rationale: str,
    ) -> Item:
        self._item_counter += 1
        item_id = f"item_{self._item_counter:04d}"
        item = Item(**draft.model_dump(), id=item_id, status=ItemStatus.DRAFT, provenance=[])
        item.provenance.append(self._provenance_event(
            agent="sme",
            action="proposed",
            target=item_id,
            rationale=f"proposed for cell topic={target_cell.topic_id} bloom={target_cell.bloom_level.value}",
        ))
        item.provenance.append(self._provenance_event(
            agent="item_writing_specialist",
            action="edited",
            target=item_id,
            rationale=cleanup_rationale,
        ))
        return item

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

    def _run_epoch(self, draft: ExamDraft, epoch_num: int) -> EpochMetrics:
        metrics = EpochMetrics(epoch=epoch_num)

        # --- 1. Critique pass: each critic visits every non-rejected item ---
        survivors = [i for i in draft.items if i.status != ItemStatus.REJECTED]
        critics: list[tuple[str, object]] = [
            ("accessibility", self._agents.accessibility),
            ("adversarial_student", self._agents.adversarial_student),
            ("psychometrician", self._agents.psychometrician),
        ]
        for critic_name, critic_agent in critics:
            for item in survivors:
                drafts: list[ObjectionDraft] = critic_agent.critique(item)  # type: ignore[attr-defined]
                for od in drafts:
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

        # --- 2. Routing pass: process each open objection raised this epoch ---
        # Snapshot the open list so resolving items doesn't mutate during iteration.
        for objection in list(draft.objections_open):
            item = self._find_item(draft, objection.target)
            if item is None or item.status == ItemStatus.REJECTED:
                # Item gone or already rejected this epoch — objection moot.
                continue

            if objection.severity == ObjectionSeverity.CRITICAL:
                # Critical: force SME edit (no rebut path). Per design §5.
                outcome = self._sme_edit_and_reverify(draft, item, objection, reason="critical_forced")
                if outcome == "accepted":
                    metrics.resolved_via_edit += 1
                    self._move_to_resolved(draft, objection)
                else:
                    metrics.items_rejected += 1
            else:
                # Non-critical: ask SME for stance first.
                rebuttal: Rebuttal = self._agents.sme.rebut_objection(item, objection)
                if rebuttal.stance == RebuttalStance.ACCEPT:
                    outcome = self._sme_edit_and_reverify(
                        draft, item, objection, reason=f"sme_accept: {rebuttal.rationale}"
                    )
                    if outcome == "accepted":
                        metrics.resolved_via_edit += 1
                        self._move_to_resolved(draft, objection)
                    else:
                        metrics.items_rejected += 1
                elif rebuttal.stance == RebuttalStance.REBUT:
                    self._append_provenance(
                        item,
                        agent="sme",
                        action="resolved",
                        rationale=f"REBUT objection {objection.id}: {rebuttal.rationale}",
                    )
                    metrics.rebutted += 1
                    # Objection stays open — Moderator could escalate after 2 rounds.
                else:  # DEFER
                    self._append_provenance(
                        item,
                        agent="sme",
                        action="resolved",
                        rationale=f"DEFER objection {objection.id}: {rebuttal.rationale}",
                    )
                    metrics.deferred += 1

        # --- 3. Convergence: count critical/high still open at end of epoch ---
        metrics.critical_high_open_at_end = sum(
            1 for o in draft.objections_open
            if o.severity in (ObjectionSeverity.CRITICAL, ObjectionSeverity.HIGH)
        )
        metrics.converged = (metrics.critical_high_open_at_end == 0)
        return metrics

    # -- Phase 3 helpers --------------------------------------------------------

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

        # Mechanical re-cleanup after the content edit.
        cleanup: EditResult = self._agents.iws.cleanup(item)
        self._replace_item_fields(item, cleanup.updated_draft)
        self._append_provenance(
            item, agent="item_writing_specialist", action="edited",
            rationale=f"[post-edit cleanup] {cleanup.rationale}",
        )

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
        Item-only fields (id, status, discrimination_est, provenance)."""
        for field_name in ItemDraft.model_fields:
            setattr(target, field_name, getattr(source, field_name))
