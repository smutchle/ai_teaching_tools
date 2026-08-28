"""Roster loading and name matching."""
from __future__ import annotations

import csv
import difflib
import os
import re

REQUIRED_FIELDS = ["last_name", "first_name", "student_id", "email"]


def load_roster(csv_path: str) -> list[dict]:
    """Load the roster CSV. Requires last_name, first_name, student_id, email
    columns (any order). Returns a list of row dicts.
    """
    if not csv_path or not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_FIELDS if c not in headers]
        if missing:
            raise ValueError(
                f"Roster CSV is missing required column(s): {', '.join(missing)}. "
                f"Required (any order): {', '.join(REQUIRED_FIELDS)}."
            )
        rows = []
        for r in reader:
            rows.append({k: (r.get(k) or "").strip() for k in REQUIRED_FIELDS})
    return rows


def display_name(row: dict) -> str:
    """'first_name last_name' — the dropdown label."""
    return f"{row['first_name']} {row['last_name']}".strip()


def roster_options(roster: list[dict]) -> list[str]:
    return [display_name(r) for r in roster]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def match_name(detected: str, roster: list[dict], cutoff: float = 0.72) -> str | None:
    """Fuzzy-match an OCR'd name to a roster entry.

    Returns the 'first last' display name, or None if no confident match
    (leave the field blank per the spec).
    """
    if not detected or not roster:
        return None
    target = _norm(detected)
    if not target:
        return None

    best_score = 0.0
    best_display: str | None = None
    for row in roster:
        disp = display_name(row)
        cand_full = _norm(disp)                       # firstlast
        cand_rev = _norm(f"{row['last_name']}{row['first_name']}")
        score = max(
            difflib.SequenceMatcher(None, target, cand_full).ratio(),
            difflib.SequenceMatcher(None, target, cand_rev).ratio(),
        )
        # Bonus if both first and last name tokens appear in the detected text.
        if _norm(row["first_name"]) in target and _norm(row["last_name"]) in target:
            score = max(score, 0.95)
        if score > best_score:
            best_score = score
            best_display = disp

    return best_display if best_score >= cutoff else None


def find_row(roster: list[dict], display: str) -> dict | None:
    for row in roster:
        if display_name(row) == display:
            return row
    return None
