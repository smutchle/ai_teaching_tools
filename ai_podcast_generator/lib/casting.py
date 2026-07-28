"""Rotating defaults for the cast: host names, voice picks, and roles.

Every session gets a seed, and the defaults for host `slot` are derived from
that seed rather than drawn fresh on each call. Streamlit reruns the whole
script on every widget interaction, so a bare `random.choice` would hand back a
different name each rerun and the form would appear to reshuffle itself while
being filled in. Deriving from a stored seed keeps a session stable and makes
re-rolling an explicit act: bump the seed.

Names are shuffled per gender and indexed by slot, so two hosts of the same
gender can never be dealt the same name. The same holds for voices, which are
drawn from every gender-labelled voice on the account - there is no quality
ranking to privilege a subset, so restricting the draw would only reduce variety.
Both pools are larger than the 3-host cap, so the modulo never wraps in practice.

Roles rotate too, but differently: there are only two of them, so rather than
indexing a shuffled pool the whole assignment is dealt at once by `roles_for`,
which guarantees the cast contains at least one of each. A conversation where
nobody knows anything, or where nobody asks, is not a conversation.
"""

from __future__ import annotations

import random

from .models import Gender
from .voices import castable_voices_for

# Deliberately excludes the ElevenLabs voice display names (Sarah, Aria, Laura,
# George, Brian, Daniel, ...) - a host called Daniel speaking in the "Daniel"
# voice reads as a bug in the picker even when it isn't.
FEMALE_NAMES: tuple[str, ...] = (
    "Maya",
    "Priya",
    "Nora",
    "Elena",
    "Ruby",
    "Simone",
    "Talia",
    "Iris",
    "Camila",
    "Yara",
)

MALE_NAMES: tuple[str, ...] = (
    "Marcus",
    "Omar",
    "Theo",
    "Julian",
    "Andre",
    "Felix",
    "Rafael",
    "Desmond",
    "Nikhil",
    "Dominic",
)

_NAME_POOL: dict[Gender, tuple[str, ...]] = {
    Gender.FEMALE: FEMALE_NAMES,
    Gender.MALE: MALE_NAMES,
}

def new_seed() -> int:
    """A fresh cast seed. Stored once per session, bumped to re-roll."""
    return random.randrange(1_000_000_000)


def _rng(seed: int, key: str) -> random.Random:
    """A generator keyed by (seed, key).

    Keying on `key` keeps the name order, the voice order and the role deal
    independent - otherwise one permutation would drive all three and slot 0
    would always pair the first name with the first voice.
    """
    return random.Random(f"{seed}:{key}")


def _shuffled(items: tuple[str, ...], seed: int, kind: str, gender: Gender) -> tuple[str, ...]:
    """Deterministic per-(seed, kind, gender) shuffle."""
    return _shuffled_by_key(items, seed, f"{kind}:{gender.value}")


def _shuffled_by_key(items: tuple[str, ...], seed: int, key: str) -> tuple[str, ...]:
    """Deterministic shuffle for pools that are not split by gender."""
    ordered = list(items)
    _rng(seed, key).shuffle(ordered)
    return tuple(ordered)


def name_for(gender: Gender, slot: int, seed: int) -> str:
    """Default host name for a slot: one of 10 per gender, distinct by slot."""
    pool = _shuffled(_NAME_POOL[gender], seed, "name", gender)
    return pool[slot % len(pool)]


def voice_ids_for(gender: Gender) -> tuple[str, ...]:
    """Every voice id the random draw may deal to a host of this gender.

    Narrower than the picker's list: see `voices.castable_voices_for`.
    """
    return tuple(v.voice_id for v in castable_voices_for(gender))


def voice_for(gender: Gender, slot: int, seed: int) -> str:
    """Default voice for a slot, drawn at random from all voices of the gender."""
    pool = _shuffled(voice_ids_for(gender), seed, "voice", gender)
    return pool[slot % len(pool)]


# Every host is a subject-matter expert. The previous scheme dealt one "expert"
# and one "prober" whose job was to ask probing questions, which produced tedious
# episodes: an interview where one voice exists to set up the other. Peers who
# both know the material argue, build, correct and go off on tangents instead.
SME_ROLE = "subject-matter expert who knows this material first-hand"

# Dealt on top of the expertise, one per host, so two experts are still distinct
# people. Each is a slant on the same material, never a reason to know less about
# it - nothing here turns a host back into an interviewer.
EXPERT_ANGLES: tuple[str, ...] = (
    "reaches for historical precedent and how the field arrived here",
    "thinks in systems and second-order effects",
    "has actually built and shipped this kind of thing",
    "explains through concrete analogy and worked example",
    "watches the numbers and says so when the evidence is thin",
    "is alert to how this fails in practice, not just on paper",
    "cares about who this lands on outside the field",
    "tracks the adjacent literature and the rival approaches",
    "is impatient with hype and wants the plain version",
    "enjoys a tangent and collects the strange detail",
)

SOLO_ROLE = (
    f"{SME_ROLE}, presenting it directly to the listener"
)


def roles_for(host_count: int, seed: int) -> tuple[str, ...]:
    """Deal a role to each host: expert first, then a distinguishing angle.

    Every host is an expert - that part does not rotate. What rotates is the
    angle each one brings, dealt distinctly so a three-host cast is three
    different experts rather than three copies of one.
    """
    if host_count <= 1:
        return (SOLO_ROLE,)

    angles = _shuffled_by_key(EXPERT_ANGLES, seed, "angle")
    return tuple(
        f"{SME_ROLE}; {angles[slot % len(angles)]}" for slot in range(host_count)
    )
