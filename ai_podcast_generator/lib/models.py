"""Typed domain model for the podcast generator.

Everything that crosses a module boundary is one of these frozen dataclasses or
enums, so the pipeline stages can be reasoned about independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Gender(str, Enum):
    """Speaker gender, used to filter the voice catalog."""

    FEMALE = "female"
    MALE = "male"




class PodcastStyle(str, Enum):
    """How the material should be framed by the hosts."""

    OVERVIEW = "overview"
    DEEP_DIVE = "deep dive"
    DEBATE = "debate"


# Measured, not assumed: render a known script and divide. At DEFAULT_SPEED with
# the current inter-turn gaps, an 838-word 42-turn script came out at 359.5s, so
# 140 words per minute including the gaps.
#
# This was 150 for a long time, inherited from Kokoro and never re-checked after
# the move to ElevenLabs - where the real rate was 128 wpm at the old settings.
# Episodes therefore ran about 17% longer than the app claimed, on top of the
# script itself running long. Re-measure with the snippet in the README if the
# speed or the gap constants change.
WORDS_PER_MINUTE = 140

@dataclass(frozen=True)
class Voice:
    """A single ElevenLabs voice, as the account reports it.

    Every field comes from `GET /v2/voices`. There is deliberately no rank or
    quality field: ElevenLabs publishes no grade, so any ordering would have been
    an opinion dressed as data, and voices are drawn at random instead.

    `gender` is the raw label - "female", "male", "neutral", or None for voices
    that carry no gender label, which is common outside the premade set. It is a
    string rather than the `Gender` enum precisely because those last two cases
    exist and the enum cannot express them.
    """

    voice_id: str
    display_name: str
    gender: str | None
    accent: str | None
    age: str | None
    use_case: str | None
    descriptor: str | None


@dataclass(frozen=True)
class Persona:
    """One podcast host: who they are and which voice speaks their lines."""

    name: str
    gender: Gender
    voice_id: str
    role: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Persona name must not be empty")
        if ":" in self.name:
            raise ValueError(
                f"Persona name {self.name!r} must not contain ':' - the colon "
                "separates speaker from dialogue in the transcript format"
            )


@dataclass(frozen=True)
class Turn:
    """A single block of dialogue attributed to one persona."""

    speaker: str
    text: str


@dataclass(frozen=True)
class Transcript:
    """An ordered sequence of dialogue turns."""

    turns: tuple[Turn, ...]

    @property
    def word_count(self) -> int:
        return sum(len(turn.text.split()) for turn in self.turns)

    @property
    def estimated_minutes(self) -> float:
        """Runtime of the finished audio, gaps included."""
        return self.word_count / WORDS_PER_MINUTE


# Supported episode lengths. Enforced here as well as in the slider so the
# invariant holds for any caller, not just the one that happens to have a widget
# in front of it.
MIN_TARGET_MINUTES = 5
MAX_TARGET_MINUTES = 60


@dataclass(frozen=True)
class PodcastSpec:
    """Everything the user chose about the episode to be produced."""

    personas: tuple[Persona, ...]
    style: PodcastStyle
    direction: str
    target_minutes: int

    def __post_init__(self) -> None:
        if not 1 <= len(self.personas) <= 3:
            raise ValueError(
                f"A podcast needs 1 to 3 hosts, got {len(self.personas)}"
            )
        if not MIN_TARGET_MINUTES <= self.target_minutes <= MAX_TARGET_MINUTES:
            raise ValueError(
                f"Target length must be {MIN_TARGET_MINUTES}-"
                f"{MAX_TARGET_MINUTES} minutes, got {self.target_minutes}"
            )
        names = [p.name.lower() for p in self.personas]
        if len(set(names)) != len(names):
            raise ValueError(f"Persona names must be distinct, got {names}")


@dataclass(frozen=True)
class PodcastPackage:
    """The finished set of deliverables for one episode."""

    audio_path: Path
    transcript: Transcript
    titles: tuple[str, ...]
    summary: str
    thumbnail_path: Path
    thumbnail_prompt: str
    duration_seconds: float
