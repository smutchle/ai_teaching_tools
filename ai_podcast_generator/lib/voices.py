"""ElevenLabs voice catalog, read from the account at runtime.

With `voices_read` granted, `GET /v2/voices` is the source of truth: it returns
the voices this account actually has, with ElevenLabs' own labels for gender,
accent, age and use case. That is strictly better than a hand-maintained list -
it picks up cloned and Voice Library additions automatically, and it does not go
stale when ElevenLabs retires a default.

The static list this replaced was wrong in both directions, which is a good
illustration of why: three of its entries (Rachel, Aria, Charlotte) are not in
this account at all - they are legacy globals that still synthesise but are not
in the library - and it was missing five that are, including a professional voice
the account owner had chosen deliberately.

`_FALLBACK_VOICES` exists only so a network blip during Step 2 does not leave the
cast picker empty. Every id in it was verified against the TTS endpoint.
"""

from __future__ import annotations

import requests

from .models import Gender, Voice

API_ROOT = "https://api.elevenlabs.io"
_TIMEOUT = 30

# ElevenLabs labels gender as "female", "male" or "neutral", and voices outside
# the premade set (a clone, a Voice Library pick) often carry no gender label at
# all. Both are usable - they just cannot be matched to a host's gender.
_NEUTRAL = "neutral"

_CATALOG: tuple[Voice, ...] | None = None


def _display_name(raw: str) -> str:
    """'Alice - Clear, Engaging Educator' -> 'Alice'.

    ElevenLabs packs a descriptor into the name field. The descriptor is worth
    keeping, but not as the thing shown in a dropdown next to two other voices.
    """
    return raw.split(" - ", 1)[0].strip() or raw.strip()


def _descriptor(raw: str) -> str | None:
    parts = raw.split(" - ", 1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None


def _is_usable(payload: dict) -> bool:
    """Whether this voice can actually be synthesised.

    A voice copied from the Voice Library keeps appearing in `GET /v2/voices`
    after its owner disables it, but TTS then fails with 403 `voice_disabled`.
    Listing it would put an entry in the picker that kills a render halfway
    through, so it is dropped here. Premade voices carry no `sharing` block at
    all, which is why the check is for the disabled status specifically rather
    than for the presence of sharing metadata.
    """
    sharing = payload.get("sharing") or {}
    return sharing.get("status") != "copied_disabled"


def _voice_from_api(payload: dict) -> Voice:
    labels = payload.get("labels") or {}
    raw_name = payload.get("name") or payload.get("voice_id", "")
    return Voice(
        voice_id=payload["voice_id"],
        display_name=_display_name(raw_name),
        gender=(labels.get("gender") or None),
        accent=(labels.get("accent") or None),
        age=(labels.get("age") or None),
        use_case=(labels.get("use_case") or None),
        descriptor=_descriptor(raw_name),
    )


def fetch_catalog(api_key: str) -> tuple[Voice, ...]:
    """Read every voice on the account. Raises on a failed request."""
    voices: list[Voice] = []
    params: dict[str, object] = {"page_size": 100}

    while True:
        response = requests.get(
            f"{API_ROOT}/v2/voices",
            headers={"xi-api-key": api_key},
            params=params,
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Could not list ElevenLabs voices ({response.status_code}): "
                f"{response.text[:300]}"
            )
        body = response.json()
        voices.extend(
            _voice_from_api(v) for v in body.get("voices", []) if _is_usable(v)
        )

        token = body.get("next_page_token")
        if not body.get("has_more") or not token:
            break
        params["next_page_token"] = token

    if not voices:
        raise RuntimeError("ElevenLabs returned an empty voice list")
    return tuple(sorted(voices, key=lambda v: v.display_name.lower()))


def ensure_catalog(api_key: str, refresh: bool = False) -> tuple[Voice, ...]:
    """Populate the process-wide catalog. Safe to call repeatedly.

    Failures are swallowed and the fallback stands in: a voice picker showing a
    known-good subset beats a stack trace on the cast step, and the failure will
    surface on the connection test anyway.
    """
    global _CATALOG
    if _CATALOG is not None and not refresh:
        return _CATALOG
    try:
        _CATALOG = fetch_catalog(api_key)
    except Exception:
        if _CATALOG is None:
            _CATALOG = _FALLBACK_VOICES
    return _CATALOG


def catalog() -> tuple[Voice, ...]:
    """The catalog as currently known, without triggering a fetch."""
    return _CATALOG if _CATALOG is not None else _FALLBACK_VOICES


def is_live() -> bool:
    """True when the catalog came from the API rather than the fallback."""
    return _CATALOG is not None and _CATALOG is not _FALLBACK_VOICES


def get_voice(voice_id: str) -> Voice:
    """Look up a voice. Raises KeyError naming the unknown id."""
    for voice in catalog():
        if voice.voice_id == voice_id:
            return voice
    raise KeyError(
        f"Unknown ElevenLabs voice {voice_id!r}; known voices are "
        f"{sorted(v.voice_id for v in catalog())}"
    )


def voices_for(gender: Gender) -> tuple[Voice, ...]:
    """Voices offerable for a host of this gender, alphabetically.

    Deliberately wider than an exact label match: a neutral voice, or one with no
    gender label at all, is still a legitimate manual choice and hiding it would
    make a voice the account owner added unreachable. The order is presentation
    only - nothing infers quality from position.
    """
    return tuple(
        v for v in catalog()
        if v.gender == gender.value or v.gender in (_NEUTRAL, None)
    )


def castable_voices_for(gender: Gender) -> tuple[Voice, ...]:
    """Voices the random cast draw may use: an exact gender-label match only.

    Narrower than `voices_for` on purpose. Dealing an unlabelled or neutral voice
    to a host the script calls "she" is a mismatch nobody asked for, so those are
    offered but never dealt automatically.
    """
    exact = tuple(v for v in catalog() if v.gender == gender.value)
    return exact or voices_for(gender)


def label_for(voice: Voice) -> str:
    """Human-readable label for the Streamlit picker."""
    facets = [
        f.replace("_", " ")
        for f in (voice.accent, voice.age, voice.use_case)
        if f
    ]
    if not facets:
        return f"{voice.display_name} (no labels)"
    return f"{voice.display_name} ({', '.join(facets)})"


# Verified against the TTS endpoint and present on the account. Used only when
# the voices endpoint cannot be reached.
_FALLBACK_VOICES: tuple[Voice, ...] = (
    Voice("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", "british", "middle_aged",
          "informative_educational", "Clear, Engaging Educator"),
    Voice("cgSgspJ2msm6clMCkdW9", "Jessica", "female", "american", "young",
          "conversational", "Playful, Bright, Warm"),
    Voice("FGY2WhTYpPnrIDTdsKH5", "Laura", "female", "american", "young",
          "social_media", "Enthusiast, Quirky Attitude"),
    Voice("XrExE9yKIg1WjnnlVkGX", "Matilda", "female", "american", "middle_aged",
          "informative_educational", "Knowledgable, Professional"),
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "female", "american", "young",
          "entertainment_tv", "Mature, Reassuring, Confident"),
    Voice("nPczCjzI2devNBz1zQrb", "Brian", "male", "american", "middle_aged",
          "social_media", "Deep, Resonant and Comfortable"),
    Voice("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", "british", "middle_aged",
          "informative_educational", "Steady Broadcaster"),
    Voice("JBFqnCBsd6RMkjVDRZzb", "George", "male", "british", "middle_aged",
          "narrative_story", "Warm, Captivating Storyteller"),
    Voice("CwhRBWXzGAHq8TQ4Fs17", "Roger", "male", "american", "middle_aged",
          "conversational", "Laid-Back, Casual, Resonant"),
    Voice("bIHbv24MWmeRgasZH58o", "Will", "male", "american", "young",
          "conversational", "Relaxed Optimist"),
)
