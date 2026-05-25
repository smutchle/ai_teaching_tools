"""Launch the `run.py` pipeline as a subprocess and track its liveness.

The Streamlit UI doesn't need to embed the Moderator — `run.py` is exactly
the entry point we want. We `Popen` it with `start_new_session=True` so the
pipeline survives if Streamlit restarts, and stash PID + cmd + started-at
into `<outputs_dir>/launch.json` for the Transcript page to pick up.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LaunchSpec:
    pdf_paths: list[Path]
    inputs_dir: Path
    outputs_dir: Path
    max_epochs: int | None = None
    skip_phase_3: bool = False
    skip_phase_4: bool = False
    # Two-tier model routing. If both are None, run.py uses the per-persona
    # registry default (all-Ollama). If both are equal, the subprocess will
    # log a uniform "PROVIDER:" line; otherwise each tier is set explicitly.
    high_provider: str | None = None
    high_model: str | None = None
    low_provider: str | None = None
    low_model: str | None = None


def _build_cmd(project_root: Path, spec: LaunchSpec) -> list[str]:
    if not spec.pdf_paths:
        raise ValueError("LaunchSpec.pdf_paths must contain at least one PDF.")
    cmd: list[str] = [
        sys.executable,
        str(project_root / "run.py"),
        "--pdf", *[str(p) for p in spec.pdf_paths],
        "--inputs-dir", str(spec.inputs_dir),
        "--outputs-dir", str(spec.outputs_dir),
    ]
    if spec.max_epochs is not None:
        cmd += ["--max-epochs", str(spec.max_epochs)]
    if spec.skip_phase_3:
        cmd.append("--skip-phase-3")
    if spec.skip_phase_4:
        cmd.append("--skip-phase-4")
    if spec.high_provider is not None:
        cmd += ["--high-provider", spec.high_provider]
    if spec.high_model is not None:
        cmd += ["--high-model", spec.high_model]
    if spec.low_provider is not None:
        cmd += ["--low-provider", spec.low_provider]
    if spec.low_model is not None:
        cmd += ["--low-model", spec.low_model]
    return cmd


def launch_run(project_root: Path, spec: LaunchSpec) -> dict[str, Any]:
    """Start the pipeline subprocess. Returns a dict with PID + paths."""
    spec.outputs_dir.mkdir(parents=True, exist_ok=True)
    log_path = spec.outputs_dir / "run.log"
    cmd = _build_cmd(project_root, spec)
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,  # survive Streamlit restarts
    )
    launch_info = {
        "pid": proc.pid,
        "cmd": cmd,
        "started_at": dt.datetime.now().isoformat(),
        "log_path": str(log_path),
        "pdf_paths": [str(p) for p in spec.pdf_paths],
        "inputs_dir": str(spec.inputs_dir),
        "max_epochs": spec.max_epochs,
    }
    (spec.outputs_dir / "launch.json").write_text(
        json.dumps(launch_info, indent=2), encoding="utf-8"
    )
    return launch_info


def is_pid_alive(pid: int) -> bool:
    """True if the process is running. False for missing AND zombie processes.

    Plain `os.kill(pid, 0)` returns success on zombies (defunct processes
    whose entry is still in the process table waiting to be reaped), which
    causes the UI to report a crashed pipeline as still running. We read
    `/proc/<pid>/status` and reject `State: Z (zombie)` explicitly. Falls
    back to `os.kill` when /proc isn't readable (non-Linux).
    """
    proc_status = f"/proc/{pid}/status"
    try:
        with open(proc_status) as f:
            for line in f:
                if line.startswith("State:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "Z":
                        return False  # zombie — parent hasn't reaped it
                    return True
        # No State: line — odd, but treat as alive.
        return True
    except FileNotFoundError:
        return False
    except (OSError, PermissionError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_launch_info(run_dir: Path) -> dict[str, Any] | None:
    """Read `launch.json` from a run directory, or None if absent."""
    path = run_dir / "launch.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def run_status(run_dir: Path) -> str:
    """One of 'running' / 'finished' / 'unknown'. Used by the Transcript page
    to surface a status badge per run."""
    info = load_launch_info(run_dir)
    if info is None:
        # Launched via CLI directly, no launch.json. We can't tell — but a
        # final-draft file means at least Phase 3 completed.
        return "finished" if (run_dir / "phase_3_final_draft.json").exists() else "unknown"
    pid = info.get("pid")
    if not isinstance(pid, int):
        return "unknown"
    return "running" if is_pid_alive(pid) else "finished"
