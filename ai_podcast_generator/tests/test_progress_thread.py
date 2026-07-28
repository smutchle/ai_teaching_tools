"""Regression test: the progress callback must run on the caller's thread.

Turns are synthesised in a thread pool. An earlier version called `progress`
from inside the worker function, which meant Streamlit's `progress.progress(...)`
was touched from a thread Streamlit did not start - it raises NoSessionContext
and the whole render dies partway through. The bug survived because every test
called synthesize_transcript from a plain script, where any thread works fine.

Run: python tests/test_progress_thread.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.models import Gender, Persona, Transcript, Turn
from lib.tts_engine import synthesize_transcript


class FakeSpeechClient:
    """Stands in for ElevenLabs: no network, no credits, deterministic audio.

    `dialogue_works=False` simulates the endpoint being unavailable, which is
    the path that falls back to synthesising each turn on its own.
    """

    def __init__(self, dialogue_works: bool = True) -> None:
        self.turns = 0
        self.dialogue_calls = 0
        self._dialogue_works = dialogue_works
        self._lock = threading.Lock()

    def speak(self, text: str, voice_id: str, previous_text=None,
              next_text=None, speed: float = 1.0) -> np.ndarray:
        with self._lock:
            self.turns += 1
        return np.full(2400, 0.001 * len(text), dtype=np.float32)

    def speak_dialogue(self, lines: list[tuple[str, str]]) -> np.ndarray:
        if not self._dialogue_works:
            raise RuntimeError("Text to Dialogue unavailable")
        with self._lock:
            self.dialogue_calls += 1
            self.turns += len(lines)
        return np.full(2400 * len(lines), 0.01, dtype=np.float32)


def run_case(dialogue_works: bool) -> tuple[list[str], list[tuple[int, int, str]], FakeSpeechClient]:
    main_thread = threading.current_thread().ident
    offending: list[str] = []
    ticks: list[tuple[int, int, str]] = []

    def progress(done: int, total: int, speaker: str) -> None:
        if threading.current_thread().ident != main_thread:
            offending.append(threading.current_thread().name)
        ticks.append((done, total, speaker))

    personas = (
        Persona(name="Maya", gender=Gender.FEMALE,
                voice_id="EXAVITQu4vr4xnSDxMaL", role="host"),
        Persona(name="Marcus", gender=Gender.MALE,
                voice_id="JBFqnCBsd6RMkjVDRZzb", role="expert"),
    )
    turns = tuple(
        Turn(speaker="Maya" if i % 2 == 0 else "Marcus", text=f"Turn {i} " + "x" * i)
        for i in range(24)
    )

    out = Path("/tmp/_progress_thread_test.mp3")
    client = FakeSpeechClient(dialogue_works=dialogue_works)
    synthesize_transcript(
        client=client,                 # type: ignore[arg-type]
        transcript=Transcript(turns=turns),
        personas=personas,
        output_path=out,
        progress=progress,
        music_path=None,               # skip music: this is about threading
        speed=1.0,                     # skip the ffmpeg tempo pass too
    )
    out.unlink(missing_ok=True)
    return offending, ticks, client


def main() -> int:
    from lib.tts_engine import _batch_turns

    turns = tuple(
        Turn(speaker="Maya" if i % 2 == 0 else "Marcus", text=f"Turn {i} " + "x" * i)
        for i in range(24)
    )
    batches = len(_batch_turns(turns))
    ok = True

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'ok  ' if condition else 'FAIL'} {label}"
              f"{'' if condition else f' -- {detail}'}")
        if not condition:
            ok = False

    print(f"dialogue path ({len(turns)} turns -> {batches} batch(es)):")
    offending, ticks, client = run_case(dialogue_works=True)
    check("progress never runs on a worker", not offending, str(sorted(set(offending))))
    check("used Text to Dialogue", client.dialogue_calls == batches,
          f"{client.dialogue_calls} calls")
    check("every turn rendered", client.turns == len(turns), str(client.turns))
    check("one tick per batch plus encode", len(ticks) == batches + 1, str(len(ticks)))
    check("progress never goes backwards",
          [d for d, _, _ in ticks] == sorted(d for d, _, _ in ticks))

    print("fallback path (dialogue endpoint unavailable):")
    offending, ticks, client = run_case(dialogue_works=False)
    check("progress never runs on a worker", not offending, str(sorted(set(offending))))
    check("fell back to per-turn synthesis", client.turns == len(turns), str(client.turns))
    check("still ticks per batch plus encode", len(ticks) == batches + 1, str(len(ticks)))

    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
