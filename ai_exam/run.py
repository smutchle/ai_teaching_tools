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


def _ag(persona: str, client, event_log, **extra):
    """Tiny helper to keep agent construction tidy. Looks up model from registry."""
    return persona, config.model_for(persona), client, event_log, extra


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
    p.add_argument("--skip-phase-3", action="store_true",
                   help="Halt after Checkpoint 2; do not run the Phase 3 critic loop.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    outputs_dir = _build_outputs_dir(args.outputs_dir)
    print(f"OUTPUTS: {outputs_dir}")

    # 1. Load JSON inputs
    course_spec = CourseSpec.model_validate(_load_json(args.inputs_dir / "course_spec.json"))
    exam_spec = ExamSpec.model_validate(_load_json(args.inputs_dir / "exam_spec.json"))
    policy = TradeOffPolicy.model_validate(_load_json(args.inputs_dir / "policy.json"))
    print(f"INPUTS: {len(course_spec.clos)} CLOs, {len(course_spec.topics)} topics, "
          f"exam={exam_spec.exam_type.value} ({exam_spec.total_points} pts)")

    # 2. Wire infrastructure
    client = config.make_anthropic_client()
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

    # 4. Instantiate agents
    sme = SMEAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("sme"),
        client=client, retriever=retriever, event_log=event_log,
    )
    ba = BlueprintArchitectAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("blueprint_architect"),
        client=client, event_log=event_log,
    )
    iws = ItemWritingSpecialistAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("item_writing_specialist"),
        client=client, event_log=event_log,
    )
    loa = LearningOutcomesAlignmentAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("learning_outcomes_alignment"),
        client=client, event_log=event_log,
    )
    grounding = GroundingVerifierAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("grounding_verifier"),
        client=client, event_log=event_log,
    )
    accessibility = AccessibilityExpertAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("accessibility"),
        client=client, event_log=event_log,
    )
    adversarial = AdversarialStudentAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("adversarial_student"),
        client=client, event_log=event_log,
    )
    psychometrician = PsychometricianAgent(
        persona_dir=_PERSONA_DIR, model=config.model_for("psychometrician"),
        client=client, event_log=event_log,
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
