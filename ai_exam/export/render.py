"""Run `quarto render` against a `.qmd` file and discover the outputs.

We render each format separately. If one format fails (e.g., LaTeX engine
missing), the others still get written; the failure is reported in the
returned `RenderResult` rather than raised, so the bundle as a whole
completes rather than aborting on the first toolchain issue.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


_QUARTO_BIN: str | None = None


def quarto_bin() -> str:
    """Resolve the `quarto` executable once and cache it."""
    global _QUARTO_BIN
    if _QUARTO_BIN is None:
        found = shutil.which("quarto")
        if not found:
            raise FileNotFoundError(
                "quarto not found on PATH; install from https://quarto.org/ "
                "or set PATH so `which quarto` resolves."
            )
        _QUARTO_BIN = found
    return _QUARTO_BIN


# Extension that quarto produces for each --to value we care about.
_FORMAT_EXT = {
    "pdf": ".pdf",
    "docx": ".docx",
    "latex": ".tex",
    "html": ".html",
}


@dataclass
class RenderResult:
    qmd_path: Path
    produced: list[Path] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # format -> stderr


def render_qmd(qmd_path: Path, formats: list[str]) -> RenderResult:
    """Render `qmd_path` to each of `formats` (one quarto invocation per format).

    Returns a RenderResult listing successful output paths and per-format
    failure messages. Does not raise on render failure; raises only on
    truly broken inputs (missing file, unknown format).
    """
    if not qmd_path.exists():
        raise FileNotFoundError(f"qmd source does not exist: {qmd_path}")
    for f in formats:
        if f not in _FORMAT_EXT:
            raise ValueError(f"unsupported format {f!r}; known: {sorted(_FORMAT_EXT)}")

    result = RenderResult(qmd_path=qmd_path)
    for fmt in formats:
        try:
            subprocess.run(
                [quarto_bin(), "render", str(qmd_path), "--to", fmt],
                check=True,
                capture_output=True,
                text=True,
                cwd=str(qmd_path.parent),
            )
        except subprocess.CalledProcessError as exc:
            # Quarto failed for this format only — log and continue with the
            # other formats. We do not bury the error: the caller gets the
            # full stderr back in result.failures and can surface it.
            result.failures[fmt] = (exc.stderr or exc.stdout or "").strip()
            continue
        out_path = qmd_path.with_suffix(_FORMAT_EXT[fmt])
        if out_path.exists():
            result.produced.append(out_path)
        else:
            result.failures[fmt] = (
                f"quarto reported success but expected output {out_path} not found"
            )
    return result
