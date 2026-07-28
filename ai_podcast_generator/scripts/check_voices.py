"""List the account's voice catalog and verify every entry can be synthesised.

Run after changing the API key's permissions, or when a render fails with a
voice error:

    python scripts/check_voices.py

The catalog is read live from `GET /v2/voices`, so this also shows what the app
will offer. It exists because the voices endpoint and the TTS endpoint disagree:
a voice copied from the Voice Library keeps being listed after its owner disables
it, and only fails when you try to speak with it. `lib/voices.py` filters those
out; this script proves the filter is still catching them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import load_settings
from lib.models import Gender
from lib.tts_engine import SpeechClient
from lib.voices import castable_voices_for, ensure_catalog, is_live, voices_for


def main() -> int:
    settings = load_settings()
    catalog = ensure_catalog(settings.eleven_labs_api_key, refresh=True)
    source = "live from the account" if is_live() else "BUILT-IN FALLBACK"
    print(f"{len(catalog)} voices ({source})\n")

    client = SpeechClient(settings.eleven_labs_api_key)
    failures: list[tuple[str, str]] = []

    for voice in catalog:
        gender = voice.gender or "unlabelled"
        try:
            client.speak("Ok.", voice.voice_id)
            status = "ok"
        except Exception as error:  # surfaced in the summary below
            status = "FAILED"
            failures.append((voice.display_name, str(error)[:120]))
        print(f"  {status:<7} {voice.display_name:<12} {gender:<11} {voice.voice_id}")

    print()
    for gender in Gender:
        offered = len(voices_for(gender))
        dealt = len(castable_voices_for(gender))
        print(f"{gender.value}: {offered} offered in the picker, {dealt} in the random draw")

    if failures:
        print(f"\n{len(failures)} voice(s) are listed but cannot be synthesised:")
        for name, error in failures:
            print(f"  {name}: {error}")
        print("\nThese would fail a render. Check the filter in lib/voices.py.")
        return 1

    print(f"\nAll {len(catalog)} voices synthesised successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
