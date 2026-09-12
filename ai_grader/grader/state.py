"""JSON-backed application state, and the isolation between sessions.

The entire UI is backed by a single state.json inside the working directory.
It can be saved/loaded at any time. Uploaded files (roster, exam, rubric,
grounding) are copied into the working dir and referenced by basename so the
whole project is self-contained and portable.

One server serves many instructors at once, so a project directory is the unit
of separation and this module is what keeps them apart:

  * Every project lives directly under one **workspace root** and is named by an
    unguessable **project code**. Nothing outside that root can be opened, so a
    typed-in path can never reach another project's state.json - or any other
    file on the server.
  * A project is **locked to one session at a time**. Two browser sessions
    driving the same state.json would interleave writes and silently lose
    pages, grades, or student assignments, so the second one is refused (and can
    take over deliberately if the first was abandoned).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import tempfile
import time
from typing import Any

from dotenv import load_dotenv

# Read .env here as well as in llm.py: AI_GRADER_WORKSPACES decides where every
# project is stored, and picking it up only because some other module happened
# to be imported first would mean silently falling back to /tmp on a server
# where it was configured.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".env"))

STATE_FILENAME = "state.json"
BACKUP_FILENAME = "state.json.bak"
LOCK_FILENAME = "session.lock"

# A lock whose heartbeat is older than this belongs to a session that is gone -
# a closed tab, a restarted server - and may be taken over. Every checkpoint
# refreshes it, so a live run (even a slow one) never goes stale.
LOCK_STALE_SECONDS = 900

# All projects live directly under this one directory and nowhere else.
DEFAULT_WORKSPACE_DIRNAME = "ai_grader_workspaces"
# Project codes are the capability that reopens a project, so they are random
# rather than sequential, and constrained to a character set with no path
# separators or dots - `..` cannot be spelled in one.
CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

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
        "pages": [],           # per-page OCR results; see grader.ocr.ocr_pages
        "evals": [],           # pages grouped into submissions; see grader.ocr.build_evals
        "curve_summary": {},   # last curve result; see grader.grading.apply_curve
    }


class ProjectLockedError(RuntimeError):
    """Another session is already working in this project."""


def workspace_root() -> str:
    """The one directory every project lives under.

    Override with AI_GRADER_WORKSPACES to put projects on a real volume instead
    of /tmp - worth doing for a shared server, where /tmp is cleared on reboot.
    """
    root = (os.environ.get("AI_GRADER_WORKSPACES") or "").strip()
    root = root or os.path.join(tempfile.gettempdir(), DEFAULT_WORKSPACE_DIRNAME)
    root = os.path.abspath(root)
    # 0700: on a shared host, other OS users have no business reading exam
    # scans or grades.
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def new_working_dir() -> str:
    """Create a fresh project directory and return its path."""
    root = workspace_root()
    for _ in range(8):
        path = os.path.join(root, secrets.token_urlsafe(9).replace("=", ""))
        if CODE_RE.fullmatch(os.path.basename(path)):
            try:
                os.mkdir(path, mode=0o700)
            except FileExistsError:
                continue
            return path
    raise RuntimeError("could not allocate a project directory")


def project_code(working_dir: str) -> str:
    """The short code that reopens this project."""
    return os.path.basename(os.path.normpath(working_dir or ""))


def resolve_code(code_or_path: str) -> str:
    """Map a project code (or the full path of one) to its directory.

    Anything that does not name a project directly inside the workspace root is
    refused, so this is the single choke point that makes a typed-in value
    unable to reach another session's project or any other file on the server.
    """
    raw = (code_or_path or "").strip().strip("/")
    if not raw:
        raise ValueError("Enter a project code.")
    code = os.path.basename(os.path.normpath(raw))
    if not CODE_RE.fullmatch(code):
        raise ValueError(
            f"{raw!r} is not a project code. A code looks like `Xk3_9aTqVb2p` and "
            "is shown in the sidebar of the session that created the project.")
    root = workspace_root()
    path = os.path.join(root, code)
    # Belt and braces: the regex already forbids separators and dots, but the
    # invariant this function exists to guarantee is worth asserting outright.
    if os.path.dirname(os.path.abspath(path)) != root:
        raise ValueError("Project codes cannot point outside the workspace.")
    return path


# ------------------------------------------------------------------- locking
def lock_path(working_dir: str) -> str:
    return os.path.join(working_dir, LOCK_FILENAME)


def read_lock(working_dir: str) -> dict[str, Any] | None:
    try:
        with open(lock_path(working_dir), encoding="utf-8") as f:
            lock = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return lock if isinstance(lock, dict) else None


def _write_lock(working_dir: str, session_id: str, claimed: float) -> None:
    os.makedirs(working_dir, exist_ok=True)
    tmp = lock_path(working_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"session": session_id, "host": socket.gethostname(),
                   "pid": os.getpid(), "claimed": claimed,
                   "heartbeat": time.time()}, f)
    os.replace(tmp, lock_path(working_dir))


def lock_is_stale(lock: dict[str, Any] | None,
                  now: float | None = None) -> bool:
    if not lock:
        return True
    now = time.time() if now is None else now
    return (now - float(lock.get("heartbeat", 0))) > LOCK_STALE_SECONDS


def held_by_other(working_dir: str, session_id: str) -> dict[str, Any] | None:
    """The live lock of a *different* session, or None if this session may write."""
    lock = read_lock(working_dir)
    if not lock or lock.get("session") == session_id or lock_is_stale(lock):
        return None
    return lock


def claim_lock(working_dir: str, session_id: str, *, force: bool = False) -> None:
    """Take this project for `session_id`. Raises ProjectLockedError if another
    live session holds it and `force` is not set."""
    other = held_by_other(working_dir, session_id)
    if other is not None and not force:
        idle = time.time() - float(other.get("heartbeat", 0))
        raise ProjectLockedError(
            f"This project is open in another session (active {int(idle)}s ago). "
            "Two sessions writing the same project overwrite each other's pages "
            "and grades.")
    _write_lock(working_dir, session_id, claimed=time.time())


def touch_lock(working_dir: str, session_id: str) -> None:
    """Heartbeat. Called on every save, which is every page and every paper."""
    lock = read_lock(working_dir)
    if lock is None or lock.get("session") == session_id:
        try:
            _write_lock(working_dir, session_id,
                        claimed=float((lock or {}).get("claimed", time.time())))
        except OSError:
            pass  # a lost heartbeat must never break a save


def release_lock(working_dir: str, session_id: str) -> None:
    lock = read_lock(working_dir)
    if lock and lock.get("session") == session_id:
        try:
            os.remove(lock_path(working_dir))
        except OSError:
            pass


def state_path(working_dir: str) -> str:
    return os.path.join(working_dir, STATE_FILENAME)


def backup_path(working_dir: str) -> str:
    return os.path.join(working_dir, BACKUP_FILENAME)


def save_state(state: dict[str, Any]) -> str:
    """Persist state to <working_dir>/state.json. Returns the path.

    The previous state.json is kept as state.json.bak first, and the new one is
    written to a temp file and moved into place, so an interrupted or failed
    save can never leave the project without a readable state to fall back on.
    """
    working_dir = state["config"]["working_dir"]
    os.makedirs(working_dir, exist_ok=True)
    path = state_path(working_dir)
    if os.path.exists(path):
        try:
            shutil.copy2(path, backup_path(working_dir))
        except OSError:
            pass  # a missing backup must never block the save itself
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def _unread_page() -> dict[str, Any]:
    """A stand-in for a page record that was never written. Mirrors the shape
    `ocr._failed_page` produces: unread, and its split boundary not to be
    trusted."""
    return {"is_new_submission": False, "is_start": True,
            "boundary_confident": False, "student_name": "", "markdown": "",
            "answers": [], "raw": "", "error": "this page was not read by the scan"}


def load_state(working_dir: str) -> dict[str, Any]:
    """Load state.json from a working dir, merging over defaults.

    Falls back to state.json.bak if the main file is unreadable, so a state.json
    truncated by a crash mid-write does not cost the whole project.
    """
    path = state_path(working_dir)
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        with open(backup_path(working_dir), encoding="utf-8") as f:
            loaded = json.load(f)
    state = default_state()
    state["config"].update(loaded.get("config", {}))
    # Force working_dir to the directory we actually loaded from.
    state["config"]["working_dir"] = working_dir
    # A scan interrupted by an older build could checkpoint a page list with
    # holes in it, and the review UI indexes straight into this list.
    state["pages"] = [p if isinstance(p, dict) else _unread_page()
                      for p in (loaded.get("pages") or [])]
    state["evals"] = loaded.get("evals", [])
    state["curve_summary"] = loaded.get("curve_summary", {})
    return state


def abspath(working_dir: str, name: str) -> str:
    """Resolve a stored basename to an absolute path in the working dir."""
    if not name:
        return ""
    if os.path.isabs(name):
        return name
    return os.path.join(working_dir, name)
