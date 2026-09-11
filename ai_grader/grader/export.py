"""Building the download bundle.

The governing rule here is that **no quiz is ever lost**. Every submission that
came out of the OCR/split step ends up in the ZIP, whatever happened to it
afterwards:

  * ``graded``        - machine-graded cleanly; annotated PDF with a grade report.
  * ``needs_grading`` - grading errored, was never run, or had nothing legible to
    grade; the original scanned pages with a hand-grading cover sheet.
  * ``unassigned``    - never matched to a roster student; same cover sheet, filed
    under the page it starts on so the instructor can identify it from the scan.

An export failure on one submission never costs the others: the pages are
re-emitted raw and the failure is recorded in the manifest.
"""
from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from typing import Callable

from . import pdfutil, roster as roster_mod

GRADED = "graded"
NEEDS_GRADING = "needs_grading"
UNASSIGNED = "unassigned"
UNACCOUNTED = "unaccounted_pages"   # scan pages in no submission at all

# Subfolder inside the ZIP for each status, and the human label for the UI.
FOLDER = {GRADED: "graded", NEEDS_GRADING: "needs_grading", UNASSIGNED: "unassigned"}
LABEL = {GRADED: "Graded", NEEDS_GRADING: "Needs hand grading",
         UNASSIGNED: "Unassigned (no student)"}

README = """This ZIP contains EVERY submission found in the scanned exam - nothing is
omitted because grading failed or was never run.

graded/
    Machine-graded papers. The original scanned pages followed by a grade
    report (score per question, comments, final score).

needs_grading/
    Papers that were NOT machine-graded - grading errored, never ran, or there
    was nothing legible to grade. Each file starts with a cover sheet stating
    why, with a blank score line, followed by the original scanned pages.
    Grade these by hand.

unaccounted_pages/
    Scanned pages that belong to NO submission - they are in no other PDF here.
    Identify the student, fix the split in the Check the scan panel and rebuild
    the download, or grade these pages by hand. This folder is absent when every
    page is accounted for, which is the normal case.

unassigned/
    Papers that were never matched to a roster student. Same cover sheet, named
    by the scan page they start on plus whatever name the OCR read. Identify
    the student from the scan, then rename the file.

manifest.csv
    One row per submission: file, status, student, scanned page range, scores,
    and any note about what went wrong.

scores.csv
    Student and final score, ready to transcribe into the gradebook. Rows that
    were not machine-graded have a blank score for you to fill in.

reconciliation.txt
    Pages of the scan not accounted for, roster students with no submission, and
    students with more than one submission. Read this before returning papers.
"""


def _slug(text: str) -> str:
    """Filename-safe token: letters and digits only."""
    return re.sub(r"[^A-Za-z0-9]+", "", text or "")


def status_of(ev: dict) -> str:
    """Which bucket a submission falls into. Unassigned always wins - a paper
    with no student cannot be handed back on the strength of a score."""
    if not ev.get("student_key"):
        return UNASSIGNED
    grade = ev.get("grade")
    if grade and not grade.get("error"):
        return GRADED
    return NEEDS_GRADING


def reason_for(ev: dict) -> str:
    """Plain-English explanation of why a submission was not machine-graded."""
    if not ev.get("student_key"):
        return ("No roster student was assigned to this submission, so it was "
                "never graded.")
    grade = ev.get("grade")
    if grade is None:
        return "Grading has not been run on this submission yet."
    if grade.get("error"):
        note = (grade.get("overall_comment") or "").strip()
        return note or "The grading request failed."
    return ""


def _base_name(ev: dict, roster: list[dict], status: str) -> str:
    if status == UNASSIGNED:
        first = (ev.get("page_indices") or [0])[0] + 1
        detected = _slug(ev.get("detected_name", ""))
        stem = f"UNASSIGNED_p{first:03d}"
        return f"{stem}_{detected}" if detected else stem
    key = ev.get("student_key") or ""
    row = roster_mod.find_row(roster, key) or {}
    last = _slug(row.get("last_name") or key or "last")
    first = _slug(row.get("first_name") or "")
    return f"{last}_{first}" if first else last


def _unique(fname: str, used: set[str]) -> str:
    """Roster names alone are not unique (there is no student id), so
    de-duplicate rather than letting one file overwrite another."""
    if fname not in used:
        used.add(fname)
        return fname
    stem, ext = os.path.splitext(fname)
    i = 2
    while f"{stem}_{i}{ext}" in used:
        i += 1
    out = f"{stem}_{i}{ext}"
    used.add(out)
    return out


def display_for(ev: dict, roster: list[dict]) -> str:
    key = ev.get("student_key") or ""
    if key:
        row = roster_mod.find_row(roster, key)
        return roster_mod.friendly_name(row or key)
    return ev.get("detected_name") or ""


def reconcile(evals: list[dict], roster: list[dict], n_pages: int) -> dict:
    """Cross-check the split against the scan and the roster.

    Everything an instructor needs to be sure no paper went missing: pages of
    the scan that belong to no submission, roster students with no submission,
    and students holding more than one.
    """
    covered: set[int] = set()
    for ev in evals:
        covered.update(ev.get("page_indices") or [])
    orphan_pages = [i for i in range(n_pages) if i not in covered]

    by_student: dict[str, list[str]] = {}
    for ev in evals:
        key = ev.get("student_key")
        if key:
            by_student.setdefault(key, []).append(ev["id"])

    missing_students = [r["name"] for r in roster if r["name"] not in by_student]
    duplicate_students = {k: v for k, v in by_student.items() if len(v) > 1}

    counts = {GRADED: 0, NEEDS_GRADING: 0, UNASSIGNED: 0}
    for ev in evals:
        counts[status_of(ev)] += 1

    return {
        "n_pages": n_pages,
        "n_pages_covered": len(covered),
        "orphan_pages": orphan_pages,
        "missing_students": missing_students,
        "duplicate_students": duplicate_students,
        "counts": counts,
        "n_submissions": len(evals),
    }


def _reconciliation_text(rec: dict) -> str:
    lines = ["RECONCILIATION", "=" * 60, ""]
    lines.append(f"Scanned pages:            {rec['n_pages']}")
    lines.append(f"Pages in a submission:    {rec['n_pages_covered']}")
    lines.append(f"Submissions:              {rec['n_submissions']}")
    lines.append(f"  graded:                 {rec['counts'][GRADED]}")
    lines.append(f"  need hand grading:      {rec['counts'][NEEDS_GRADING]}")
    lines.append(f"  unassigned:             {rec['counts'][UNASSIGNED]}")
    lines.append("")

    if rec["orphan_pages"]:
        lines.append("PAGES BELONGING TO NO SUBMISSION:")
        lines.append("  " + ", ".join(str(i + 1) for i in rec["orphan_pages"]))
        lines.append("  Exported on their own under unaccounted_pages/.")
    else:
        lines.append("Every scanned page is included in exactly one submission PDF.")
    lines.append("")

    if rec["missing_students"]:
        lines.append("ROSTER STUDENTS WITH NO SUBMISSION:")
        for name in rec["missing_students"]:
            lines.append(f"  {name}")
        lines.append("")
        lines.append("  If one of these students did sit the exam, their paper is")
        lines.append("  probably in unassigned/ or merged into another student's PDF.")
    else:
        lines.append("Every roster student has a submission.")
    lines.append("")

    if rec["duplicate_students"]:
        lines.append("STUDENTS WITH MORE THAN ONE SUBMISSION (likely a bad split):")
        for name, ids in rec["duplicate_students"].items():
            lines.append(f"  {name}: {', '.join(ids)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_bundle(exam_path: str, evals: list[dict], roster: list[dict], *,
                 max_points: int, out_dir: str,
                 progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Render every submission and pack them into a ZIP.

    Returns ``{"zip_bytes", "rows", "reconciliation", "counts", "failures"}``.
    A submission that cannot be rendered at all is still written out as its raw
    scanned pages and flagged in the manifest, so it reaches the instructor.
    """
    os.makedirs(out_dir, exist_ok=True)
    n_pages = pdfutil.page_count(exam_path) if os.path.exists(exam_path) else 0

    used: set[str] = set()
    rows: list[dict] = []
    failures: list[str] = []
    total = len(evals)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, ev in enumerate(evals):
            status = status_of(ev)
            folder = FOLDER[status]
            base = _base_name(ev, roster, status)
            fname = _unique(f"{base}.pdf", used)
            arcname = f"{folder}/{fname}"

            sub_dir = os.path.join(out_dir, folder)
            os.makedirs(sub_dir, exist_ok=True)
            out_path = os.path.join(sub_dir, fname)

            pages = ev.get("page_indices") or []
            display = display_for(ev, roster)
            grade = ev.get("grade") or {}
            note = ""

            try:
                if status == GRADED:
                    pdfutil.annotate_graded_pdf(
                        exam_path, pages, grade, display, max_points, out_path)
                else:
                    pdfutil.write_ungraded_pdf(
                        exam_path, pages, out_path,
                        student_display=display, max_points=max_points,
                        reason=reason_for(ev),
                        detected_name=ev.get("detected_name", ""),
                        partial_grade=grade if grade.get("questions") else None,
                    )
            except Exception as e:  # noqa: BLE001 - a render failure must not lose the paper
                note = f"Render failed ({type(e).__name__}: {e}); raw pages only."
                failures.append(f"{ev.get('id')}: {e}")
                try:
                    pdfutil.split_eval_pdf(exam_path, pages, out_path)
                except Exception as e2:  # noqa: BLE001
                    note = f"COULD NOT EXPORT: {type(e2).__name__}: {e2}"
                    failures.append(f"{ev.get('id')} (raw fallback): {e2}")
                    out_path = ""

            if out_path and os.path.exists(out_path):
                zf.write(out_path, arcname=arcname)
            else:
                arcname = ""

            rows.append({
                "file": arcname,
                "status": status,
                "student": ev.get("student_key", ""),
                "detected_name": ev.get("detected_name", ""),
                "submission_id": ev.get("id", ""),
                # Numeric columns stay numeric-or-None (never ""), so the manifest
                # survives Arrow serialization in the UI; csv writes None as blank.
                "first_page": (pages[0] + 1) if pages else None,
                "last_page": (pages[-1] + 1) if pages else None,
                "n_pages": len(pages),
                "raw_score": grade.get("raw_total") if status == GRADED else None,
                "curve_added": grade.get("curve_added") if status == GRADED else None,
                "final_score": grade.get("final_total") if status == GRADED else None,
                "max_points": max_points,
                "note": note or reason_for(ev),
            })

            if progress:
                progress(i, total, f"Exported {fname} ({i + 1}/{total})")

        rec = reconcile(evals, roster, n_pages)

        # Pages in no submission are somebody's work too: export them rather
        # than only naming them in reconciliation.txt.
        if rec["orphan_pages"]:
            fname = "unaccounted_pages.pdf"
            arcname = f"{UNACCOUNTED}/{fname}"
            sub_dir = os.path.join(out_dir, UNACCOUNTED)
            os.makedirs(sub_dir, exist_ok=True)
            out_path = os.path.join(sub_dir, fname)
            note = ("These scan pages belong to no submission. Fix the split in the "
                    "Check the scan panel and rebuild the download, or grade them "
                    "by hand.")
            try:
                pdfutil.write_unaccounted_pdf(exam_path, rec["orphan_pages"], out_path)
            except Exception as e:  # noqa: BLE001
                note = f"COULD NOT EXPORT: {type(e).__name__}: {e}"
                failures.append(f"unaccounted pages: {e}")
                arcname = ""
            if arcname and os.path.exists(out_path):
                zf.write(out_path, arcname=arcname)
                rows.append({
                    "file": arcname, "status": UNACCOUNTED, "student": "",
                    "detected_name": "", "submission_id": "",
                    "first_page": rec["orphan_pages"][0] + 1,
                    "last_page": rec["orphan_pages"][-1] + 1,
                    "n_pages": len(rec["orphan_pages"]),
                    "raw_score": None, "curve_added": None, "final_score": None,
                    "max_points": max_points, "note": note,
                })

        manifest = io.StringIO()
        writer = csv.DictWriter(manifest, fieldnames=list(rows[0].keys()) if rows else
                                ["file", "status", "student", "note"])
        writer.writeheader()
        writer.writerows(rows)
        zf.writestr("manifest.csv", manifest.getvalue())

        scores = io.StringIO()
        sw = csv.writer(scores)
        sw.writerow(["student", "final_score", "max_points", "status", "file"])
        for r in rows:
            sw.writerow([r["student"] or r["detected_name"] or r["submission_id"],
                         r["final_score"], max_points, r["status"], r["file"]])
        zf.writestr("scores.csv", scores.getvalue())

        zf.writestr("reconciliation.txt", _reconciliation_text(rec))
        zf.writestr("README.txt", README)

    buf.seek(0)
    return {"zip_bytes": buf.getvalue(), "rows": rows, "reconciliation": rec,
            "counts": rec["counts"], "failures": failures}
