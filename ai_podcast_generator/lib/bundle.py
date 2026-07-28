"""Assemble the deliverables into a single ZIP for download."""

from __future__ import annotations

import io
import re
import zipfile

from .models import PodcastPackage, PodcastSpec
from .transcript_io import render_transcript


def slugify(text: str, max_length: int = 60) -> str:
    """Filesystem-safe stem derived from the episode title."""
    cleaned = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
    return (slug[:max_length].rstrip("-")) or "podcast"


def build_titles_text(titles: tuple[str, ...]) -> str:
    return "\n".join(f"{i}. {title}" for i, title in enumerate(titles, start=1))


def build_show_notes(
    package: PodcastPackage, spec: PodcastSpec, chosen_title: str
) -> str:
    """Human-readable summary of the episode and how it was produced."""
    minutes, seconds = divmod(int(round(package.duration_seconds)), 60)
    cast = "\n".join(
        f"  - {p.name} ({p.gender.value}, {p.role}) - voice: {p.voice_id}"
        for p in spec.personas
    )
    return f"""\
{chosen_title}

DESCRIPTION
{package.summary}

RUNTIME
{minutes}m {seconds}s

ALTERNATE TITLES
{build_titles_text(package.titles)}

CAST
{cast}

PRODUCTION
  Style: {spec.style.value}
  Direction: {spec.direction}
  Voices: ElevenLabs
  Cover art: Z-Image Turbo (zimage)

COVER ART PROMPT
{package.thumbnail_prompt}
"""


def build_zip(
    package: PodcastPackage, spec: PodcastSpec, chosen_title: str
) -> tuple[bytes, str]:
    """Bundle audio, transcript, titles, summary, notes and cover art.

    Returns the ZIP bytes and the suggested download filename.
    """
    stem = slugify(chosen_title)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(package.audio_path, f"{stem}/{stem}.mp3")
        archive.write(package.thumbnail_path, f"{stem}/{stem}-cover.png")
        archive.writestr(
            f"{stem}/transcript.txt", render_transcript(package.transcript)
        )
        archive.writestr(f"{stem}/titles.txt", build_titles_text(package.titles))
        archive.writestr(f"{stem}/summary.txt", package.summary)
        archive.writestr(
            f"{stem}/show-notes.txt", build_show_notes(package, spec, chosen_title)
        )

    return buffer.getvalue(), f"{stem}.zip"
