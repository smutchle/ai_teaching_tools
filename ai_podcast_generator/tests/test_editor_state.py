"""Regression test: the turn editor must never show a previous script.

Streamlit ignores `value=`/`index=` once a keyed widget exists. The turn editor
originally keyed its widgets by position (`turn_text_3`), so writing a new script
left turn 3 of the *old* one on screen - and because that makes the editor think
the user edited something, it offered "Apply turn edits", which would write the
stale text back under the new cast's names. That is the "old text and wrong
characters" report.

Needs no network or API keys: it only drives the review step's widgets.

Run: python tests/test_editor_state.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest

from lib.models import Gender, Persona, PodcastSpec, PodcastStyle

APP = str(Path(__file__).resolve().parent.parent / "podcast_app.py")


def spec_for(first: str, second: str) -> PodcastSpec:
    return PodcastSpec(
        personas=(
            Persona(name=first, gender=Gender.FEMALE,
                    voice_id="EXAVITQu4vr4xnSDxMaL", role="prober"),
            Persona(name=second, gender=Gender.MALE,
                    voice_id="JBFqnCBsd6RMkjVDRZzb", role="expert"),
        ),
        style=PodcastStyle.OVERVIEW, direction="d", target_minutes=5,
    )


def script(first: str, second: str, marker: str, turns: int = 3) -> str:
    speakers = [first, second]
    return "\n\n".join(
        f"{speakers[i % 2]}: {marker} line {i}." for i in range(turns)
    )


def load(at: AppTest, spec: PodcastSpec, text: str) -> AppTest:
    at.session_state["spec"] = spec
    at.session_state["transcript_text"] = text
    at.run()
    return at


def turn_text(at: AppTest) -> list[str]:
    return [w.value for w in at.text_area if (w.key or "").startswith("turn_text_")]


def turn_speakers(at: AppTest) -> list[str]:
    return [s.value for s in at.selectbox if (s.key or "").startswith("turn_speaker_")]


def main() -> int:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["step"] = 3
    at.session_state["connection_ok"] = True

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'} {label}{'' if condition else f' -- {detail}'}")
        if not condition:
            failures.append(label)

    print("episode A, then a rewrite with a completely new cast:")
    load(at, spec_for("Maya", "Marcus"), script("Maya", "Marcus", "FIRST"))
    check("A renders its own text", all("FIRST" in t for t in turn_text(at)), str(turn_text(at)))

    load(at, spec_for("Nora", "Theo"), script("Nora", "Theo", "SECOND"))
    check("B shows no text from A", not any("FIRST" in t for t in turn_text(at)), str(turn_text(at)))
    check("B speakers are B's cast", set(turn_speakers(at)) <= {"Nora", "Theo"}, str(turn_speakers(at)))
    check("no spurious Apply button",
          not any(b.label == "Apply turn edits" for b in at.button))

    print("rewrite keeping one host (the case that mislabels characters):")
    load(at, spec_for("Nora", "Marcus"), script("Nora", "Marcus", "THIRD"))
    check("C shows no earlier text",
          all("THIRD" in t for t in turn_text(at)), str(turn_text(at)))
    check("C speakers are C's cast", set(turn_speakers(at)) <= {"Nora", "Marcus"},
          str(turn_speakers(at)))

    print("rewrite with a different number of turns:")
    load(at, spec_for("Nora", "Marcus"), script("Nora", "Marcus", "FOURTH", turns=6))
    texts = turn_text(at)
    check("turn count follows the new script", len(texts) == 6, f"{len(texts)} widgets")
    check("D shows no earlier text", all("FOURTH" in t for t in texts), str(texts))

    print("editing is still preserved across an unrelated rerun:")
    at.text_area(key=[w.key for w in at.text_area if (w.key or "").startswith("turn_text_")][0])\
      .set_value("EDITED line").run()
    check("typed edit survives", "EDITED line" in turn_text(at), str(turn_text(at)[:2]))
    check("Apply offered after a real edit",
          any(b.label == "Apply turn edits" for b in at.button))

    print()
    print("FAILED: " + ", ".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
