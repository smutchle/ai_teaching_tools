"""Parse and render the editable transcript format.

The wire format is deliberately plain so Step 3 can hand it to a text editor:

    Maya: Welcome back to the show. Today we're digging into...
    Daniel: And I have opinions.

A turn starts at a line beginning with a known persona name followed by a colon.
Any following lines that don't start a new turn are appended to the current one,
so users can hard-wrap freely while editing.
"""

from __future__ import annotations

import re

from .models import Persona, Transcript, Turn

# Leading '**' tolerates an LLM emitting markdown bold around the speaker name.
_SPEAKER_LINE = re.compile(r"^\s*\**\s*([^:*\n]{1,60}?)\s*\**\s*:\s*(.*)$")


def parse_transcript(text: str, personas: tuple[Persona, ...]) -> Transcript:
    """Parse the editable format into turns.

    Speaker names are matched case-insensitively against `personas`. Lines whose
    prefix isn't a known persona are treated as continuation text rather than
    silently dropped, so a stray "Note: ..." never deletes user content.
    """
    by_lower: dict[str, str] = {p.name.lower(): p.name for p in personas}

    turns: list[Turn] = []
    current_speaker: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_speaker is None:
            return
        body = " ".join(part.strip() for part in buffer if part.strip()).strip()
        if body:
            turns.append(Turn(speaker=current_speaker, text=body))

    for line in text.splitlines():
        match = _SPEAKER_LINE.match(line)
        candidate = match.group(1).strip().lower() if match else ""
        if match and candidate in by_lower:
            flush()
            current_speaker = by_lower[candidate]
            buffer = [match.group(2)]
        elif current_speaker is not None:
            buffer.append(line)
        # Text before the first recognised speaker (preamble, stray headings) is
        # dropped: it has no voice to be spoken in.

    flush()

    if not turns:
        raise ValueError(
            "No dialogue turns found. Each line must start with a host name and "
            f"a colon, e.g. '{personas[0].name}: ...'. "
            f"Expected one of: {', '.join(p.name for p in personas)}"
        )
    return Transcript(turns=tuple(turns))


def render_transcript(transcript: Transcript) -> str:
    """Render turns back to the editable format."""
    return "\n\n".join(f"{turn.speaker}: {turn.text}" for turn in transcript.turns)


# eleven_v3 interprets bracketed audio tags rather than reading them aloud, and
# they are the model's main expressive lever - stability and style are coarse by
# comparison. This used to be a four-tag whitelist (laughs / laughing / sighs /
# exhales) with everything else deleted, which left the synthesiser with almost
# no delivery direction to work from and is the likeliest single cause of a flat,
# lifeless read.
#
# Two kinds of bracketed text still have to go, because they are NOT tags and v3
# will happily speak them:
#   - citation and reference markers bled in from source material: [12], [3, 4],
#     [Smith 2020]
#   - prose stage directions a writer might type: [he pauses for a long moment]
#
# Both are separable from real tags by SHAPE rather than by membership of a list:
# a performance tag is short, alphabetic, and one to three words. That structural
# test is what `_is_audio_tag` applies, so a tag this module has never heard of
# still reaches the synthesiser instead of being silently deleted.

# The documented vocabulary. Used by the prompt and as the worked example of what
# "looks like a tag" means - deliberately NOT enforced as a closed set.
AUDIO_TAGS: frozenset[str] = frozenset({
    # involuntary reactions
    "laughs", "laughing", "chuckles", "giggles", "sighs", "exhales", "inhales",
    "gasps", "scoffs", "groans", "clears throat",
    # emotional colour
    "excited", "amused", "curious", "skeptical", "sceptical", "surprised",
    "thoughtful", "hesitant", "impressed", "frustrated", "sympathetic",
    "sarcastic", "deadpan", "wry", "serious", "warmly", "nervous", "dismissive",
    # delivery
    "whispers", "softly", "quietly", "emphatic", "slowly", "quickly", "flatly",
    "drawn out", "rushed", "trails off",
})

# Transcription artefacts and sound cues that pass the shape test but must not
# survive: the first two are noise from user-supplied transcripts, the rest would
# have the model attempt a sound effect in the middle of a sentence.
_NOT_TAGS: frozenset[str] = frozenset({
    "inaudible", "unintelligible", "crosstalk", "music", "applause",
    "silence", "static", "beep",
})

_TAG = re.compile(r"\[([^\]]{0,60})\]")

# One to three lowercase words, nothing else. Rejects anything with a digit
# (citations), and anything longer (prose directions).
_TAG_SHAPE = re.compile(r"^[a-z]+(?: [a-z]+){0,2}$")


def _is_audio_tag(inner: str) -> bool:
    """Whether bracketed text is a performance tag rather than a citation."""
    tag = inner.strip().lower()
    if tag in _NOT_TAGS:
        return False
    return len(tag) <= 24 and bool(_TAG_SHAPE.match(tag))


def strip_stage_directions(text: str) -> str:
    """Remove citations and prose directions, keeping performance tags intact."""

    def keep_known(match: re.Match[str]) -> str:
        return match.group(0) if _is_audio_tag(match.group(1)) else " "

    without_square = _TAG.sub(keep_known, text)
    without_round = re.sub(
        r"\((?:laughs?|chuckles?|sighs?|pauses?|beat|music|laughing)[^)]{0,40}\)",
        " ",
        without_square,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", without_round).strip()


def clean_for_speech(transcript: Transcript) -> Transcript:
    """Drop stage directions and any turn left empty by that removal."""
    cleaned: list[Turn] = []
    for turn in transcript.turns:
        body = strip_stage_directions(turn.text)
        if body:
            cleaned.append(Turn(speaker=turn.speaker, text=body))
    if not cleaned:
        raise ValueError("Transcript contains no speakable text after cleaning")
    return Transcript(turns=tuple(cleaned))
