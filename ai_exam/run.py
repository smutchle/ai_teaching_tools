"""CLI entry point: Phase 0–2 debug pipeline.

Usage:
    python run.py --inputs-dir test_data --pdf test_data/pchem_notes.pdf
    python run.py --inputs-dir test_data --pdf test_data/pchem_notes.pdf --outputs-dir runs/foo

Loads JSON inputs (course_spec.json, exam_spec.json, policy.json) plus the
specified PDF, ingests into a per-run Chroma collection, runs the Moderator
through Checkpoint 2, and writes events.jsonl, calls/, per-phase snapshots,
and a transcript.md under the outputs directory.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import config
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
from events import EventLog
from models import CourseSpec, ExamSpec, ItemStatus
from moderator import AgentRoster, Moderator, TradeOffPolicy
from retrieval import ChromaRetriever, OllamaEmbedder, ingest_pdf


_PROJECT_ROOT = Path(__file__).resolve().parent
_PERSONA_DIR = _PROJECT_ROOT / "persona"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_outputs_dir(parent: Path | None) -> Path:
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
        return parent
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = _PROJECT_ROOT / "runs" / f"run_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="ai_exam Phase 0–2 debug pipeline")
    p.add_argument("--inputs-dir", type=Path, default=_PROJECT_ROOT / "test_data",
                   help="Directory containing course_spec.json, exam_spec.json, policy.json")
    p.add_argument("--pdf", type=Path, required=True,
                   help="Path to the source PDF to ingest")
    p.add_argument("--outputs-dir", type=Path, default=None,
                   help="Directory for events, snapshots, Chroma store. Default: runs/run_<ts>/")
    p.add_argument("--themes-target-count", type=int, default=12)
    p.add_argument("--chunks-per-cell", type=int, default=6)
    p.add_argument("--max-epochs", type=int, default=None,
                   help="Override policy.max_epochs for the Phase 3 epoch loop. "
                        "Useful for debug runs where you want to cap at 1 or 2.")
    p.add_argument("--provider", choices=["ollama", "arc", "anthropic"], default=None,
                   help="Route every agent to one provider (uses that provider's "
                        "default model). Shortcut for setting both tiers equal.")
    p.add_argument("--high-provider", choices=["ollama", "arc", "anthropic"], default=None,
                   help="Provider for HIGH-tier agents (SME, Blueprint Architect, "
                        "Adversarial Student). Overrides --provider when given.")
    p.add_argument("--high-model", default=None,
                   help="Model id for HIGH-tier agents. If omitted, uses the "
                        "high-provider's default.")
    p.add_argument("--low-provider", choices=["ollama", "arc", "anthropic"], default=None,
                   help="Provider for LOW-tier agents (IWS, LOA, Grounding, "
                        "Accessibility, Psychometrician).")
    p.add_argument("--low-model", default=None,
                   help="Model id for LOW-tier agents.")
    p.add_argument("--skip-phase-3", action="store_true",
                   help="Halt after Checkpoint 2; do not run the Phase 3 critic loop.")
    p.add_argument("--skip-phase-4", action="store_true",
                   help="Halt after Phase 3; do not run audit/variants/export bundle.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    outputs_dir = _build_outputs_dir(args.outputs_dir)
    print(f"OUTPUTS: {outputs_dir}")

    # Apply provider override (if any) BEFORE any agent is constructed, so
    # the per-persona MODEL_REGISTRY lookup sees the overridden values.
    # Per-tier flags take precedence over the shorthand --provider.
    high_p = args.high_provider or args.provider
    low_p = args.low_provider or args.provider
    if high_p is not None or low_p is not None:
        # If only one tier was specified, default the other to the same so
        # the user gets uniform behavior when they leave the other blank.
        high_p = high_p or low_p
        low_p = low_p or high_p
        high_choice = config.make_choice(high_p, args.high_model)
        low_choice = config.make_choice(low_p, args.low_model)
        config.override_tiers(high_choice, low_choice)
        print(f"PROVIDER: high → {high_choice.provider}/{high_choice.model}, "
              f"low → {low_choice.provider}/{low_choice.model}")

    # 1. Load JSON inputs
    course_spec = CourseSpec.model_validate(_load_json(args.inputs_dir / "course_spec.json"))
    exam_spec = ExamSpec.model_validate(_load_json(args.inputs_dir / "exam_spec.json"))
    policy = TradeOffPolicy.model_validate(_load_json(args.inputs_dir / "policy.json"))
    print(f"INPUTS: {len(course_spec.clos)} CLOs, {len(course_spec.topics)} topics, "
          f"exam={exam_spec.exam_type.value} ({exam_spec.total_points} pts)")

    # 2. Wire infrastructure
    event_log = EventLog(outputs_dir / "events")
    embedder = OllamaEmbedder(host=config.OLLAMA_HOST, model=config.OLLAMA_EMBED_MODEL)
    retriever = ChromaRetriever(
        persist_dir=outputs_dir / "chroma",
        collection_name="course_materials",
        embedder=embedder,
    )

    # 3. Ingest PDF into Chroma
    print(f"INGEST: {args.pdf.name}")
    corpus = ingest_pdf(args.pdf, retriever)
    print(f"        {len(corpus)} chunks indexed (collection count = {retriever.count()})")

    # 4. Instantiate agents. Each agent gets its own provider instance bound
    #    to (provider_kind, model_id) per config.MODEL_REGISTRY.
    sme = SMEAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("sme"),
        retriever=retriever, event_log=event_log,
    )
    ba = BlueprintArchitectAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("blueprint_architect"),
        event_log=event_log,
    )
    iws = ItemWritingSpecialistAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("item_writing_specialist"),
        event_log=event_log,
    )
    loa = LearningOutcomesAlignmentAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("learning_outcomes_alignment"),
        event_log=event_log,
    )
    grounding = GroundingVerifierAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("grounding_verifier"),
        event_log=event_log,
    )
    accessibility = AccessibilityExpertAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("accessibility"),
        event_log=event_log,
    )
    adversarial = AdversarialStudentAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("adversarial_student"),
        event_log=event_log,
    )
    psychometrician = PsychometricianAgent(
        persona_dir=_PERSONA_DIR, provider=config.make_provider("psychometrician"),
        event_log=event_log,
    )
    roster = AgentRoster(
        sme=sme, blueprint_architect=ba, iws=iws, loa=loa,
        grounding=grounding, accessibility=accessibility,
        adversarial_student=adversarial, psychometrician=psychometrician,
    )

    # 5. Run Moderator through Checkpoint 2
    print("RUN: Moderator Phase 0–2 ...")
    moderator = Moderator(
        course_spec=course_spec,
        exam_spec=exam_spec,
        policy=policy,
        retriever=retriever,
        full_corpus=corpus,
        agents=roster,
        event_log=event_log,
        outputs_dir=outputs_dir,
        themes_target_count=args.themes_target_count,
        chunks_per_cell=args.chunks_per_cell,
    )
    draft = moderator.run_through_checkpoint_2()
    print(f"      Phase 2 done: {len(draft.items)} items / "
          f"{sum(it.points for it in draft.items)} pts")

    # 5b. Phase 3 refinement loop (unless skipped)
    if not args.skip_phase_3:
        max_ep = args.max_epochs if args.max_epochs is not None else policy.max_epochs
        print(f"RUN: Moderator Phase 3 (epoch loop, max_epochs={max_ep}) ...")
        draft = moderator.run_through_checkpoint_3(draft, max_epochs_override=args.max_epochs)
    else:
        print("RUN: Phase 3 skipped (--skip-phase-3)")

    # 5c. Phase 4: audit + variants + export bundle (unless skipped)
    phase_4_result = None
    if not args.skip_phase_3 and not args.skip_phase_4:
        print("RUN: Moderator Phase 4 (audit → variants → export) ...")
        phase_4_result = moderator.run_phase_4(draft)
        print(f"      audit: {len(phase_4_result.audit.objections)} exam-level objections, "
              f"{len(phase_4_result.audit.report.imbalance_notes)} imbalance notes")
        print(f"      variants: {len(phase_4_result.variants)}")
        print(f"      export bundle: {len(phase_4_result.export_paths)} files written")
    else:
        print("RUN: Phase 4 skipped" + (" (--skip-phase-3)" if args.skip_phase_3 else " (--skip-phase-4)"))

    # 6. Summary + transcript
    survivors = [it for it in draft.items if it.status != ItemStatus.REJECTED]
    print()
    print(f"DONE: {len(survivors)} items surviving "
          f"({len(draft.items) - len(survivors)} rejected) "
          f"across {len(draft.blueprint.cells)} cells")
    accepted_points = sum(it.points for it in survivors)
    print(f"      {accepted_points} pts (target {exam_spec.total_points})")
    by_bloom: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for it in draft.items:
        by_bloom[it.bloom_level.value] = by_bloom.get(it.bloom_level.value, 0) + 1
        by_status[it.status.value] = by_status.get(it.status.value, 0) + 1
    print(f"      Bloom distribution (all items): {by_bloom}")
    print(f"      Status distribution: {by_status}")
    if not args.skip_phase_3:
        print(f"      Objections: {len(draft.objections_open)} open, "
              f"{len(draft.objections_resolved)} resolved")

    transcript_path = outputs_dir / "transcript.md"
    transcript_path.write_text(event_log.to_markdown(), encoding="utf-8")
    print(f"      events:     {event_log.events_path}")
    print(f"      sidecars:   {event_log.calls_dir}/ ({len(list(event_log.calls_dir.glob('*.json')))} files)")
    print(f"      transcript: {transcript_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
