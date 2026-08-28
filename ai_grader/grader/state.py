"""JSON-backed application state.

The entire UI is backed by a single state.json inside the working directory.
It can be saved/loaded at any time. Uploaded files (roster, exam, rubric,
grounding) are copied into the working dir and referenced by basename so the
whole project is self-contained and portable.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

STATE_FILENAME = "state.json"

DEFAULT_ADDITIONAL_INSTRUCTIONS = (
    "Allow some deviation from the rubric on short-answer if the answer fits the "
    "course material.  Add constructive comments that reward critical thinking skills."
)


def default_config() -> dict[str, Any]:
    return {
        "api_key_override": "",
        "working_dir": "",
        "quiz_name": "",
        "roster_csv": "",          # basename inside working dir
        "exam_pdf": "",            # basename inside working dir
        "rubric_pdf": "",          # basename inside working dir
        "grounding_pdfs": [],      # list of basenames inside working dir
        "max_points": 100,
        "min_points": 0,
        "curve_min_avg": 90,       # optional; None disables the curve
        "additional_instructions": DEFAULT_ADDITIONAL_INSTRUCTIONS,
    }


def default_state() -> dict[str, Any]:
    return {
        "config": default_config(),
        "evals": [],   # populated by OCR; see grader.ocr for the schema
    }


def new_working_dir() -> str:
    """Create a fresh working directory under /tmp and return its path."""
    return tempfile.mkdtemp(prefix="ai_grader_")


def state_path(working_dir: str) -> str:
    return os.path.join(working_dir, STATE_FILENAME)


def save_state(state: dict[str, Any]) -> str:
    """Persist state to <working_dir>/state.json. Returns the path."""
    working_dir = state["config"]["working_dir"]
    os.makedirs(working_dir, exist_ok=True)
    path = state_path(working_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_state(working_dir: str) -> dict[str, Any]:
    """Load state.json from a working dir, merging over defaults."""
    path = state_path(working_dir)
    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)
    state = default_state()
    state["config"].update(loaded.get("config", {}))
    # Force working_dir to the directory we actually loaded from.
    state["config"]["working_dir"] = working_dir
    state["evals"] = loaded.get("evals", [])
    return state


def abspath(working_dir: str, name: str) -> str:
    """Resolve a stored basename to an absolute path in the working dir."""
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    return os.path.join(working_dir, name)
