"""Roster loading and name matching.

The roster is a **single-column CSV** of student names, one per line, written
"last, first" (quoted, since the name itself contains a comma):

    "Nguyen, Emily"
    "O'Brien, Sean Patrick"

A header row is optional — a first row that is a generic column label (``name``,
``student``, ...) and carries no comma is skipped. Names read off the scanned
papers by the vision model are never assumed to match a roster entry exactly;
:func:`match_name` fuzzy-matches and returns ``None`` when it is not confident.
"""
from __future__ import annotations

import csv
import difflib
import os
import re

# A lone first row equal to one of these (case-insensitive) is treated as a
# header, not a student. Anything containing a comma is always data.
HEADER_LABELS = {
    "name", "names", "student", "students", "student name", "student_name",
    "full name", "full_name",
}


def _split_name(raw: str) -> tuple[str, str]:
    """Split a roster cell into ``(last_name, first_name)``.

    "Nguyen, Emily" -> ("Nguyen", "Emily"). Without a comma we fall back to
    treating the final whitespace-separated token as the last name.
    """
    raw = (raw or "").strip()
    if "," in raw:
        last, _, first = raw.partition(",")
        return last.strip(), first.strip()
    parts = raw.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return raw, ""


def _make_row(raw: str) -> dict:
    last, first = _split_name(raw)
    return {"name": raw.strip(), "last_name": last, "first_name": first}


def load_roster(csv_path: str) -> list[dict]:
    """Load the single-column roster CSV.

    Returns a list of ``{"name", "last_name", "first_name"}`` dicts in file
    order. Only the first column of each row is read; blank rows are skipped.
    """
    if not csv_path or not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        cells = [(r[0].strip() if r else "") for r in csv.reader(f)]
    cells = [c for c in cells if c]
    if not cells:
        raise ValueError(
            "Roster CSV is empty. Expected one student name per line, "
            'written "last, first" (in double quotes).'
        )
    if "," not in cells[0] and cells[0].lower() in HEADER_LABELS:
        cells = cells[1:]
    return [_make_row(c) for c in cells]


def display_name(row: dict) -> str:
    """The canonical key / dropdown label — the roster name verbatim."""
    return row["name"]


def friendly_name(row_or_display: dict | str) -> str:
    """'First Last' — for prompts, PDF headers and other prose."""
    if isinstance(row_or_display, dict):
        last, first = row_or_display["last_name"], row_or_display["first_name"]
    else:
        last, first = _split_name(row_or_display)
    return f"{first} {last}".strip()


def roster_options(roster: list[dict]) -> list[str]:
    return [display_name(r) for r in roster]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def match_name(detected: str, roster: list[dict], cutoff: float = 0.72) -> str | None:
    """Fuzzy-match an OCR'd name to a roster entry.

    Returns the roster display name ("last, first"), or None if no confident
    match (leave the field blank per the spec).
    """
    if not detected or not roster:
        return None
    target = _norm(detected)
    if not target:
        return None

    best_score = 0.0
    best_display: str | None = None
    for row in roster:
        last, first = _norm(row["last_name"]), _norm(row["first_name"])
        score = max(
            difflib.SequenceMatcher(None, target, first + last).ratio(),
            difflib.SequenceMatcher(None, target, last + first).ratio(),
        )
        # Bonus if both first and last name tokens appear in the detected text.
        if first and last and first in target and last in target:
            score = max(score, 0.95)
        # A one-word roster entry (or one-word OCR read) still matches on the
        # surname alone, but less confidently.
        elif last and last in target:
            score = max(score, 0.80)
        if score > best_score:
            best_score = score
            best_display = display_name(row)

    return best_display if best_score >= cutoff else None


def find_row(roster: list[dict], display: str) -> dict | None:
    for row in roster:
        if display_name(row) == display:
            return row
    return None
