"""Orchestrates the generation stages behind Step 4.

Kept out of the Streamlit module so the pipeline can be exercised headlessly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .llm_client import LLMClient
from .models import PodcastPackage, PodcastSpec, Transcript
from .script_writer import (
    generate_summary,
    generate_thumbnail_prompt,
    generate_titles,
)
from .transcript_io import clean_for_speech
from .tts_engine import DEFAULT_SPEED, SpeechClient, synthesize_transcript
from .zimage_client import ZImageClient

# (stage label, fraction complete 0.0-1.0)
StageCallback = Callable[[str, float], None]

THUMBNAIL_SIZE = 1024
THUMBNAIL_STEPS = 8


def produce_podcast(
    client: LLMClient,
    zimage: ZImageClient,
    speech: SpeechClient,
    spec: PodcastSpec,
    transcript: Transcript,
    output_dir: Path,
    music_path: Path | None = None,
    speed: float = DEFAULT_SPEED,
    on_stage: StageCallback | None = None,
) -> PodcastPackage:
    """Render audio, then write titles, summary and cover art.

    Audio runs first because it is the deliverable the user is waiting on and
    the one most likely to surface a bad transcript.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    def stage(label: str, fraction: float) -> None:
        if on_stage is not None:
            on_stage(label, fraction)

    speakable = clean_for_speech(transcript)

    stage("Synthesising speech", 0.02)
    audio_path = output_dir / "podcast.mp3"
    total_turns = len(speakable.turns)

    def turn_progress(index: int, total: int, speaker: str) -> None:
        # Speech occupies the first 60% of the progress bar.
        fraction = 0.02 + 0.58 * (index / max(total, 1))
        label = (
            "Encoding audio"
            if speaker == "encoding"
            else f"Synthesising speech - turn {index + 1} of {total} ({speaker})"
        )
        stage(label, fraction)

    duration = synthesize_transcript(
        client=speech,
        transcript=speakable,
        personas=spec.personas,
        output_path=audio_path,
        speed=speed,
        progress=turn_progress,
        music_path=music_path,
    )

    stage("Writing episode titles", 0.65)
    titles = generate_titles(client, transcript)

    stage("Writing episode summary", 0.75)
    summary = generate_summary(client, transcript)

    stage("Designing cover art", 0.82)
    thumbnail_prompt = generate_thumbnail_prompt(
        client, transcript, titles[0], summary
    )

    stage("Rendering cover art", 0.9)
    thumbnail_path = zimage.generate_image(
        prompt=thumbnail_prompt,
        width=THUMBNAIL_SIZE,
        height=THUMBNAIL_SIZE,
        num_steps=THUMBNAIL_STEPS,
    ).save(output_dir / "cover.png")

    stage("Done", 1.0)
    return PodcastPackage(
        audio_path=audio_path,
        transcript=transcript,
        titles=titles,
        summary=summary,
        thumbnail_path=thumbnail_path,
        thumbnail_prompt=thumbnail_prompt,
        duration_seconds=duration,
    )
