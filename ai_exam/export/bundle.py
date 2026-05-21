"""Phase 4 export bundle builder.

Takes the final ExamDraft + ExamAudit + ItemVariants and produces the export
bundle: `.qmd` sources for the exam, answer key, rubrics, variants, and audit
report, each rendered to the user-requested formats (PDF / DOCX / LaTeX),
plus a `provenance.json` with the full audit trail.

Layout under `outputs_dir/`:

  exam_bundle/
    exam.qmd, exam.pdf, exam.docx, exam.tex
    answer_key.qmd, answer_key.pdf, answer_key.docx, answer_key.tex
    rubrics.qmd, rubrics.pdf, rubrics.docx, rubrics.tex   (only if any rubrics)
    exam_report.qmd, exam_report.pdf, exam_report.docx
    variants/
      <item_id>_<kind>.qmd, .pdf, .docx, .tex
    provenance.json
    render_failures.json   (only if any format failed)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from models import CourseSpec, ExamAudit, ExamDraft, ExamSpec, ItemVariant

from export import templates
from export.render import RenderResult, render_qmd


_DEFAULT_FORMATS = ["pdf", "docx", "latex"]
_REPORT_FORMATS = ["pdf", "docx"]


@dataclass
class ExportBundle:
    """Summary of what was produced. `failures` is keyed by qmd stem."""

    bundle_dir: Path
    produced: list[Path] = field(default_factory=list)
    failures: dict[str, dict[str, str]] = field(default_factory=dict)


def build_export_bundle(
    *,
    draft: ExamDraft,
    audit: ExamAudit,
    variants: list[ItemVariant],
    exam_spec: ExamSpec,
    course_spec: CourseSpec,
    outputs_dir: Path,
    formats: list[str] = _DEFAULT_FORMATS,
) -> ExportBundle:
    """Write the .qmd sources and render each one. Return paths + failures.

    Errors from a single format don't abort the bundle; they're collected and
    written to `render_failures.json` for the user to see. The function
    raises only on programmer errors (e.g., missing quarto binary, bad inputs).
    """
    bundle_dir = outputs_dir / "exam_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    variants_dir = bundle_dir / "variants"

    summary = ExportBundle(bundle_dir=bundle_dir)

    # --- write + render the four primary documents -----------------------

    def _emit(name: str, qmd_body: str, fmts: list[str]) -> None:
        qmd_path = bundle_dir / f"{name}.qmd"
        qmd_path.write_text(qmd_body, encoding="utf-8")
        summary.produced.append(qmd_path)
        result = render_qmd(qmd_path, fmts)
        summary.produced.extend(result.produced)
        if result.failures:
            summary.failures[name] = result.failures

    _emit("exam", templates.build_exam_qmd(draft, exam_spec), formats)
    _emit(
        "answer_key",
        templates.build_answer_key_qmd(draft, exam_spec, course_spec),
        formats,
    )
    _emit(
        "instructor_notes",
        templates.build_instructor_notes_qmd(draft, exam_spec, course_spec),
        formats,
    )

    rubrics_qmd = templates.build_rubrics_qmd(draft, exam_spec)
    if rubrics_qmd is not None:
        _emit("rubrics", rubrics_qmd, formats)

    _emit(
        "exam_report",
        templates.build_audit_report_qmd(draft, audit, exam_spec),
        _REPORT_FORMATS,
    )

    # --- variants -------------------------------------------------------

    if variants:
        variants_dir.mkdir(parents=True, exist_ok=True)
        items_by_id = {it.id: it for it in draft.items}
        for v in variants:
            base = items_by_id.get(v.base_item_id)
            if base is None:
                # Should not happen — variants are generated from surviving
                # items. Surface as a soft failure rather than crashing the
                # whole bundle.
                summary.failures.setdefault("variants", {})[
                    f"{v.base_item_id}_{v.kind.value}"
                ] = f"base item {v.base_item_id} not found in draft"
                continue
            stem = f"{v.base_item_id}_{v.kind.value}"
            qmd_path = variants_dir / f"{stem}.qmd"
            qmd_path.write_text(
                templates.build_variant_qmd(v, base, exam_spec), encoding="utf-8"
            )
            summary.produced.append(qmd_path)
            result: RenderResult = render_qmd(qmd_path, formats)
            summary.produced.extend(result.produced)
            if result.failures:
                summary.failures[f"variants/{stem}"] = result.failures

    # --- provenance.json ------------------------------------------------

    provenance_path = bundle_dir / "provenance.json"
    payload = {
        "draft": draft.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        "variants": [v.model_dump(mode="json") for v in variants],
    }
    provenance_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    summary.produced.append(provenance_path)

    # --- render_failures.json (only if any failed) -----------------------

    if summary.failures:
        failures_path = bundle_dir / "render_failures.json"
        failures_path.write_text(
            json.dumps(summary.failures, indent=2), encoding="utf-8"
        )
        summary.produced.append(failures_path)

    return summary
