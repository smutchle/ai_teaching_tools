"""Regression tests for per-topic state: cast, and the output directory.

Two behaviours that pull in opposite directions and are easy to break together:

1. A new topic deals a fresh cast. Different source material should not open
   with the same two people.
2. A cast the user edited by hand survives walking to Step 3 and back. Streamlit
   discards the state of widgets it did not render, so without mirroring the
   values outside widget state a rename silently reverts - leaving a cast on
   screen that disagrees with the script just written from it.

3. Each topic gets its own output directory. Episodes used to share one
   per-session directory, so a second topic silently overwrote the first one's
   podcast.mp3 and cover.png on disk.

Needs no network or API keys.

Run: python tests/test_cast_state.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "podcast_app.py")


def cast(at: AppTest) -> list[tuple[str, str, str]]:
    names = [w.value for w in at.text_input if (w.key or "").startswith("name_")]
    voices = [s.value.display_name for s in at.selectbox
              if (s.key or "").startswith("voice_")]
    roles = [w.value for w in at.text_input if (w.key or "").startswith("role_")]
    return list(zip(names, voices, roles))


def at_step_2(at: AppTest, source: str) -> AppTest:
    at.session_state["raw_transcript"] = source
    at.run()
    return at


def round_trip(at: AppTest) -> AppTest:
    """Walk to the review step and back, as a user reviewing a draft would."""
    at.session_state["step"] = 3
    at.run()
    at.session_state["step"] = 2
    at.run()
    return at


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'} {label}"
              f"{'' if condition else f' -- {detail}'}")
        if not condition:
            failures.append(label)

    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["step"] = 2
    at.session_state["connection_ok"] = True
    at.session_state["source_mode"] = "Existing transcript"

    print("a fresh cast per topic:")
    first = cast(at_step_2(at, "TOPIC ONE: tokenizer drift."))
    check("a plain rerun does not re-deal", cast(at.run()) == first, str(cast(at)))
    check("a round trip does not re-deal", cast(round_trip(at)) == first, str(cast(at)))

    second = cast(at_step_2(at, "TOPIC TWO: analytical chemistry."))
    check("new source deals a new cast", second != first, f"{first} vs {second}")

    third = cast(at_step_2(at, "TOPIC THREE: something else again."))
    check("and again on a third topic", third != second, f"{second} vs {third}")

    at.session_state["source_mode"] = "PDF source material"
    at.session_state["documents"] = ()
    at.run()
    check("switching source mode counts as a new topic", cast(at) != third)

    print("hand edits survive navigation:")
    at.session_state["source_mode"] = "Existing transcript"
    at_step_2(at, "TOPIC FOUR: the editing case.")
    name_key = [w.key for w in at.text_input if (w.key or "").startswith("name_")][0]
    role_key = [w.key for w in at.text_input if (w.key or "").startswith("role_")][0]
    voice_key = [s.key for s in at.selectbox if (s.key or "").startswith("voice_")][0]

    at.text_input(key=name_key).set_value("Bartholomew").run()
    at.text_input(key=role_key).set_value("resident cynic").run()
    at.selectbox(key=voice_key).select_index(4).run()
    edited = cast(at)[0]

    round_trip(at)
    check("edited name survives", cast(at)[0][0] == "Bartholomew", str(cast(at)[0]))
    check("edited voice survives", cast(at)[0][1] == edited[1], str(cast(at)[0]))
    check("edited role survives", cast(at)[0][2] == "resident cynic", str(cast(at)[0]))

    print("but a new topic still overrides them:")
    at_step_2(at, "TOPIC FIVE: back to a fresh deal.")
    check("edits discarded on a new topic", cast(at)[0][0] != "Bartholomew", str(cast(at)[0]))

    print("a new output directory per topic:")
    import shutil
    root = Path(__file__).resolve().parent.parent / "output"
    at_step_2(at, "TOPIC SIX: directory one.")
    first_dir = at.session_state["episode_id"]
    (root / first_dir).mkdir(parents=True, exist_ok=True)
    (root / first_dir / "podcast.mp3").write_bytes(b"EPISODE-SIX")

    at.run()
    check("a rerun keeps the directory", at.session_state["episode_id"] == first_dir)
    round_trip(at)
    check("a round trip keeps the directory",
          at.session_state["episode_id"] == first_dir)

    at_step_2(at, "TOPIC SEVEN: directory two.")
    second_dir = at.session_state["episode_id"]
    check("a new topic opens a new directory", second_dir != first_dir,
          f"{first_dir} vs {second_dir}")
    check("the previous episode's audio survives",
          (root / first_dir / "podcast.mp3").read_bytes() == b"EPISODE-SIX")
    check("ids sort chronologically", first_dir < second_dir,
          f"{first_dir} !< {second_dir}")
    check("the stale package is dropped", at.session_state["package"] is None)
    for stale in (first_dir, second_dir):
        shutil.rmtree(root / stale, ignore_errors=True)

    print("and Shuffle cast still re-deals:")
    before = cast(at)
    [b for b in at.button if b.label == "Shuffle cast"][0].click().run()
    check("shuffle re-deals", cast(at) != before, str(cast(at)))

    print()
    print("FAILED: " + ", ".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
