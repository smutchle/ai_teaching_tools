"""ElevenLabs speech synthesis, intro music, and audio assembly.

Turns are synthesised individually and stitched with gaps rather than sent as one
block, because each turn needs its own voice. Two things keep that from sounding
like clips glued together:

- `previous_text` / `next_text` are sent with every request, so the model knows
  what was said either side of the line and lands the prosody accordingly.
- A speaker change gets a longer beat than the same speaker continuing.

Audio comes back as raw PCM (`pcm_24000`), not MP3, so there is nothing to decode
per turn - the bytes go straight into a numpy array. `pcm_44100` returns 403 on
this account; 24 kHz is well above what speech needs.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from .models import Persona, Transcript, Turn
from .voices import get_voice

API_ROOT = "https://api.elevenlabs.io/v1"

# pcm_24000 is 16-bit signed little-endian mono at 24 kHz.
SAMPLE_RATE = 24_000
_OUTPUT_FORMAT = "pcm_24000"
_PCM_DTYPE = "<i2"
_PCM_FULL_SCALE = 32768.0

# eleven_v3 is ElevenLabs' most expressive model and the one they recommend for
# multi-speaker dialogue, which is exactly this workload.
DEFAULT_MODEL = "eleven_v3"
MUSIC_MODEL = "music_v2"

# Text to Dialogue renders a whole exchange in one request, so the model sees the
# turns around each line and matches prosody and turn-taking across them. That is
# the thing per-turn synthesis structurally cannot do: every line was previously
# generated blind to the conversation it sits in, which is what makes stitched
# dialogue sound like people reading at each other. v3 is the only model it
# accepts.
#
# The documented ceiling is 2000 characters across all inputs[].text. Batching at
# turn boundaries keeps prosody continuous within a batch; a 5-minute episode is
# about three batches rather than thirty isolated turns.
DIALOGUE_MODEL = "eleven_v3"
DIALOGUE_CHAR_LIMIT = 2_000

# The v3 family differs from every other model in two ways this module cares
# about: it rejects previous_text / next_text outright ("Providing previous_text
# or next_text is not yet supported with the 'eleven_v3' model", 400
# unsupported_model), and stability is its only live voice setting. Both checks
# key off this set. Expressiveness is worth more here than cross-turn prosody
# continuity, so v3 stays the default and the context is simply omitted for it.
_V3_MODELS = frozenset({"eleven_v3", "eleven_ttv_v3"})

# Stability is the ONLY delivery dial eleven_v3 has, and it takes three
# discrete modes: 0.0 "Creative" (most emotional and expressive, occasionally
# hallucinates a stray sound), 0.5 "Natural" (balanced, closest to the original
# recording), and 1.0 "Robust" (v2-like, "less responsive to directional
# prompts" - it largely ignores audio tags).
#
# This value has walked down the scale as each mode proved too flat in the ear:
# Robust bought turn-to-turn evenness by making the read woodenly literal, and
# Natural still produced a read the account owner called lifeless. Creative is
# the mode the v3 prompting guide pairs with audio tags ("for maximum
# expressiveness with audio tags, use Creative or Natural"). The known cost is
# the occasional hallucinated noise, which a regenerate fixes; if that trade
# sours, 0.5 is the fallback.
V3_STABILITY = 0.0

# The v2-family dials. Per the voice-settings docs, similarity, style, speed
# and speaker boost are each "not available for the Eleven v3 model" - the API
# accepts them silently and ignores them - so v3 requests send stability alone
# and this dict serves only non-v3 models.
DEFAULT_VOICE_SETTINGS: dict[str, object] = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}

# Each turn is its own request and comes back at its own level - measured across
# a six-turn stretch the spread was about 6 dB, which no voice setting changes
# because it is not a delivery choice, it is per-request normalisation. Matching
# every turn to the median before stitching is what actually evens the dynamics.
# The clamp stops a near-silent clip being hauled up to the target and bringing
# its noise floor with it.
_LOUDNESS_MATCH_LIMIT_DB = 9.0

# Delivery speed, applied after synthesis with ffmpeg's atempo filter rather
# than asked of the model.
#
# `voice_settings.speed` is accepted by both endpoints and does nothing on
# eleven_v3. Measured, three samples each on identical text: 0.7 gave 6.88 /
# 6.80 / 6.24s, 1.0 gave 6.40 / 7.44 / 6.16s, 1.2 gave 6.64 / 6.56 / 6.80s -
# entirely generation noise, where 0.7 against 1.2 should differ by most of a
# factor of two. Text to Dialogue has no speed control at all.
#
# atempo resamples without shifting pitch, works whatever the model does, and
# applies to speech only - the intro music keeps its own tempo.
DEFAULT_SPEED = 1.08

# Gaps between turns. A speaker change gets a slightly longer beat than the same
# speaker continuing, which is what makes stitched turns read as conversation
# rather than as clips glued together.
#
# These were 0.28 / 0.14 and read as stilted: real speakers come in far faster
# than a third of a second after each other, and often overlap. Halving them is
# the bigger half of the pace fix - a gap is dead air, whereas raising the speed
# only compresses speech that was already playing.
_GAP_SPEAKER_CHANGE = 0.16
_GAP_SAME_SPEAKER = 0.07
_LEAD_OUT = 0.8

# Turns are independent requests, so they parallelise. Creator-tier concurrency
# is limited and a 500-turn episode would take 15 minutes strictly sequentially;
# this is deliberately below the tier cap to leave room for retries.
_MAX_WORKERS = 4
_TIMEOUT = 180
_RETRIES = 3

# --- intro music -----------------------------------------------------------
# One clip, generated once and reused for every episode: the brief asks for the
# same fade-in each time, and regenerating per episode would spend music credits
# for no audible benefit.
MUSIC_PROMPT = (
    "Warm, understated instrumental podcast intro. Soft electric piano and a "
    "gentle synth pad over a slow, steady pulse. Curious and thoughtful rather "
    "than triumphant. No drums build, no risers, no vocals, no sound effects."
)
MUSIC_SECONDS = 7.0
_MUSIC_FADE_IN = 2.0
_MUSIC_FADE_OUT = 3.0
# The speech starts before the music has finished, so the bed ducks away under
# the first line instead of stopping dead and leaving a hole.
_MUSIC_OVERLAP = 2.0
_MUSIC_LEVEL = 0.5

# (turns finished, total turns, speaker just finished). Always invoked on the
# caller's thread - see the note in synthesize_transcript. Callers drive
# Streamlit widgets from this, and Streamlit rejects widget writes from threads
# it did not start.
ProgressCallback = Callable[[int, int, str], None]


class SpeechClient:
    """ElevenLabs text-to-speech and music, scoped to one API key."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise ValueError("ElevenLabs API key is empty")
        self._api_key = api_key
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self._api_key, "Content-Type": "application/json"}

    def check(self) -> str:
        """Synthesise two words to prove the key and model work.

        There is no cheap health endpoint to use instead: this key has neither
        `user_read` nor `models_read`, so the only way to know the credentials
        work is to make the call the app actually depends on.
        """
        audio = self.speak("Connection test.", get_voice_id_for_check())
        return f"{self._model}, {len(audio) / SAMPLE_RATE:.1f}s sample returned"

    def speak(
        self,
        text: str,
        voice_id: str,
        previous_text: str | None = None,
        next_text: str | None = None,
        speed: float = 1.0,
    ) -> np.ndarray:
        """Render one turn to a mono float32 waveform.

        `speed` is forwarded for models that honour it; eleven_v3 does not.
        Episode pacing is set by `_apply_tempo` after synthesis instead.
        """
        # v3 honours stability alone; the other dials are v2-only and ignored.
        settings: dict[str, object] = (
            {"stability": V3_STABILITY}
            if self._model in _V3_MODELS
            else {**DEFAULT_VOICE_SETTINGS, "speed": speed}
        )
        payload: dict[str, object] = {
            "text": text,
            "model_id": self._model,
            "voice_settings": settings,
        }
        # Prosody context, where the model accepts it. Omitted rather than sent
        # as empty strings, which read as "there was silence here".
        if self._model not in _V3_MODELS:
            if previous_text:
                payload["previous_text"] = previous_text
            if next_text:
                payload["next_text"] = next_text

        last_error: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                response = requests.post(
                    f"{API_ROOT}/text-to-speech/{voice_id}",
                    params={"output_format": _OUTPUT_FORMAT},
                    headers=self._headers(),
                    json=payload,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as error:
                last_error = error
                continue

            if response.status_code == 200:
                pcm = np.frombuffer(response.content, dtype=_PCM_DTYPE)
                if pcm.size == 0:
                    raise RuntimeError(
                        f"ElevenLabs returned no audio for voice {voice_id!r} "
                        f"on text: {text[:120]!r}"
                    )
                return (pcm.astype(np.float32) / _PCM_FULL_SCALE)

            # 429 is a concurrency limit rather than a quota wall; the others are
            # transient. Anything else is a real error and should not be retried.
            if response.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"ElevenLabs TTS failed ({response.status_code}) for voice "
                    f"{voice_id!r}: {response.text[:300]}"
                )
            last_error = RuntimeError(
                f"{response.status_code}: {response.text[:200]}"
            )

        raise RuntimeError(
            f"ElevenLabs TTS failed after {_RETRIES} attempts for voice "
            f"{voice_id!r}: {last_error}"
        )

    def speak_dialogue(self, lines: list[tuple[str, str]]) -> np.ndarray:
        """Render a whole exchange in one request. `lines` is (voice_id, text).

        The per-line `speed` that `speak` accepts has no equivalent here, and no
        effect there either - see `_apply_tempo`.
        """
        payload: dict[str, object] = {
            "inputs": [{"text": text, "voice_id": vid} for vid, text in lines],
            "model_id": DIALOGUE_MODEL,
            # The OpenAPI spec gives `settings` exactly one field, stability;
            # anything else sent here is ignored. Request-global, so per-turn
            # delivery is steered entirely by the audio tags in each line.
            "settings": {"stability": V3_STABILITY},
        }

        last_error: Exception | None = None
        for _ in range(_RETRIES):
            try:
                response = requests.post(
                    f"{API_ROOT}/text-to-dialogue",
                    params={"output_format": _OUTPUT_FORMAT},
                    headers=self._headers(),
                    json=payload,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as error:
                last_error = error
                continue

            if response.status_code == 200:
                pcm = np.frombuffer(response.content, dtype=_PCM_DTYPE)
                if pcm.size == 0:
                    raise RuntimeError("Text to Dialogue returned no audio")
                return pcm.astype(np.float32) / _PCM_FULL_SCALE

            if response.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"Text to Dialogue failed ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            last_error = RuntimeError(
                f"{response.status_code}: {response.text[:200]}"
            )

        raise RuntimeError(
            f"Text to Dialogue failed after {_RETRIES} attempts: {last_error}"
        )

    def compose_music(self, seconds: float = MUSIC_SECONDS) -> bytes:
        """Generate an instrumental clip. Returns MP3 bytes."""
        response = requests.post(
            f"{API_ROOT}/music",
            headers=self._headers(),
            json={
                "prompt": MUSIC_PROMPT,
                "music_length_ms": int(seconds * 1000),
                "model_id": MUSIC_MODEL,
                "force_instrumental": True,
            },
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"ElevenLabs music generation failed "
                f"({response.status_code}): {response.text[:300]}"
            )
        return response.content


def get_voice_id_for_check() -> str:
    """A known-good voice for the connection test.

    Reads whatever the catalog currently holds rather than a pinned id, so the
    test exercises a voice this account actually has.
    """
    from .voices import catalog

    return catalog()[0].voice_id


def intro_music(client: SpeechClient, cache_path: Path) -> np.ndarray:
    """The shared intro bed, generated once and cached on disk.

    Cached as a 24 kHz mono WAV rather than as the raw MP3 so that loading it
    costs a file read: it is already at the sample rate the episode is assembled
    at, so there is no resampling step at render time.
    """
    if cache_path.exists():
        audio, rate = sf.read(cache_path, dtype="float32")
        if rate == SAMPLE_RATE:
            return np.asarray(audio, dtype=np.float32)
        # A cache written at another rate is stale; fall through and rebuild.

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path = cache_path.with_suffix(".source.mp3")
    mp3_path.write_bytes(client.compose_music())

    # ffmpeg rather than a Python resampler: scipy is not a dependency here, and
    # the conversion has to be exact or the bed will drift against the speech.
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp3_path),
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            str(cache_path),
        ],
        check=True,
        capture_output=True,
    )
    mp3_path.unlink()

    audio, _ = sf.read(cache_path, dtype="float32")
    return np.asarray(audio, dtype=np.float32)


def _shaped_intro(music: np.ndarray) -> np.ndarray:
    """Trim the clip and apply the fade in and fade out."""
    wanted = int(MUSIC_SECONDS * SAMPLE_RATE)
    bed = music[:wanted].astype(np.float32).copy()
    if bed.size == 0:
        return bed

    fade_in = min(int(_MUSIC_FADE_IN * SAMPLE_RATE), bed.size)
    fade_out = min(int(_MUSIC_FADE_OUT * SAMPLE_RATE), bed.size - fade_in)
    if fade_in > 0:
        bed[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    if fade_out > 0:
        bed[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
    return bed * _MUSIC_LEVEL


def synthesize_transcript(
    client: SpeechClient,
    transcript: Transcript,
    personas: tuple[Persona, ...],
    output_path: Path,
    speed: float = DEFAULT_SPEED,
    progress: ProgressCallback | None = None,
    music_path: Path | None = None,
) -> float:
    """Render the whole transcript to an MP3. Returns duration in seconds.

    `music_path` is the cache location for the intro bed. Music is best-effort:
    the brief calls it non-critical, and an episode without an intro is a far
    better outcome than a failed render, so a music failure is swallowed.
    """
    voice_by_speaker = {p.name: p.voice_id for p in personas}
    for name in {turn.speaker for turn in transcript.turns}:
        if name not in voice_by_speaker:
            raise KeyError(
                f"Transcript references speaker {name!r} with no matching persona. "
                f"Cast is: {', '.join(voice_by_speaker)}"
            )
        get_voice(voice_by_speaker[name])  # validate the voice id up front

    turns = transcript.turns
    total = len(turns)
    batches = _batch_turns(turns)

    def render(index: int) -> np.ndarray:
        """One batch, as a single Text to Dialogue request."""
        batch = batches[index]
        lines = [(voice_by_speaker[turn.speaker], turn.text) for turn in batch]
        try:
            return client.speak_dialogue(lines)
        except Exception:
            # Falling back keeps an episode renderable if the dialogue endpoint
            # is unavailable. It sounds worse - each line generated blind to its
            # neighbours - which is the whole reason for the batching above.
            parts: list[np.ndarray] = []
            for position, turn in enumerate(batch):
                if position:
                    gap = (
                        _GAP_SAME_SPEAKER
                        if turn.speaker == batch[position - 1].speaker
                        else _GAP_SPEAKER_CHANGE
                    )
                    parts.append(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
                parts.append(
                    client.speak(turn.text, voice_by_speaker[turn.speaker])
                )
            return np.concatenate(parts)

    # Batches are independent requests, so they still parallelise. `progress` is
    # called from this thread, never a worker - see the note below.
    clips: list[np.ndarray | None] = [None] * len(batches)
    finished_turns = 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        pending = {pool.submit(render, i): i for i in range(len(batches))}
        for future in as_completed(pending):
            index = pending[future]
            clips[index] = future.result()
            finished_turns += len(batches[index])
            if progress is not None:
                progress(
                    min(finished_turns, total) - 1, total, batches[index][-1].speaker
                )

    if any(clip is None for clip in clips):  # unreachable: result() re-raises
        raise RuntimeError("Internal error: a batch produced no audio")
    rendered = _match_loudness([clip for clip in clips if clip is not None])

    # Only the seams BETWEEN batches need a gap now. Inside a batch the model
    # places its own turn transitions, which is the point of using it.
    pieces: list[np.ndarray] = []
    for index, clip in enumerate(rendered):
        if index > 0:
            pieces.append(
                np.zeros(int(_GAP_SPEAKER_CHANGE * SAMPLE_RATE), dtype=np.float32)
            )
        pieces.append(clip)
    pieces.append(np.zeros(int(_LEAD_OUT * SAMPLE_RATE), dtype=np.float32))
    speech = np.concatenate(pieces)

    if progress is not None:
        progress(total, total, "encoding")

    # Speech only: the bed keeps its own tempo, and stretching music alongside
    # dialogue would detune it.
    speech = _apply_tempo(speech, speed)
    audio = _with_intro(speech, client, music_path)

    # Normalise to a consistent peak so episodes don't vary wildly in loudness.
    peak = float(np.max(np.abs(audio)))
    if peak > 0.0:
        audio = audio * (0.95 / peak)

    _write_mp3(audio, output_path)
    return len(audio) / SAMPLE_RATE


def _with_intro(
    speech: np.ndarray, client: SpeechClient, music_path: Path | None
) -> np.ndarray:
    """Mix the faded intro bed in front of the speech, overlapping the tail."""
    if music_path is None:
        return speech
    try:
        bed = _shaped_intro(intro_music(client, music_path))
    except Exception:
        # Non-critical by design: ship the episode without an intro rather than
        # failing a render that is otherwise complete.
        return speech
    if bed.size == 0:
        return speech

    overlap = min(int(_MUSIC_OVERLAP * SAMPLE_RATE), bed.size)
    speech_start = bed.size - overlap
    out = np.zeros(speech_start + speech.size, dtype=np.float32)
    out[: bed.size] += bed
    out[speech_start:] += speech
    return out


def _batch_turns(turns: tuple[Turn, ...]) -> list[list[Turn]]:
    """Group consecutive turns into Text to Dialogue requests.

    Splits only at turn boundaries and packs each batch as full as the character
    limit allows, because every split is a seam where prosody restarts. A single
    turn over the limit becomes its own batch rather than being cut mid-sentence.
    """
    batches: list[list[Turn]] = []
    current: list[Turn] = []
    size = 0

    for turn in turns:
        length = len(turn.text)
        if current and size + length > DIALOGUE_CHAR_LIMIT:
            batches.append(current)
            current, size = [], 0
        current.append(turn)
        size += length

    if current:
        batches.append(current)
    return batches


def _rms(clip: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(clip)))) if clip.size else 0.0


def _match_loudness(clips: list[np.ndarray]) -> list[np.ndarray]:
    """Bring every turn to the same loudness as the median turn.

    Deliberately the median rather than a fixed target: it keeps the episode at
    the level the model actually produced, so this only removes the spread
    between turns instead of also changing the overall volume.
    """
    levels = [_rms(clip) for clip in clips]
    speaking = [level for level in levels if level > 1e-4]
    if not speaking:
        return clips

    target = float(np.median(speaking))
    ceiling = 10.0 ** (_LOUDNESS_MATCH_LIMIT_DB / 20.0)
    floor = 1.0 / ceiling

    matched: list[np.ndarray] = []
    for clip, level in zip(clips, levels):
        if level <= 1e-4:
            matched.append(clip)
            continue
        gain = min(max(target / level, floor), ceiling)
        matched.append(clip * gain)
    return matched


def _apply_tempo(audio: np.ndarray, speed: float) -> np.ndarray:
    """Time-stretch without shifting pitch, via ffmpeg's atempo filter.

    A phase vocoder is not something to hand-roll in numpy, and atempo is exact
    and already a dependency. Values outside atempo's 0.5-2.0 window would need
    chaining; the episode range never approaches either end.
    """
    if abs(speed - 1.0) < 0.01:
        return audio

    with tempfile.TemporaryDirectory() as work:
        raw = Path(work) / "speech.wav"
        stretched = Path(work) / "stretched.wav"
        sf.write(raw, audio, SAMPLE_RATE)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(raw),
                "-filter:a", f"atempo={speed:.3f}",
                str(stretched),
            ],
            check=True,
            capture_output=True,
        )
        out, _ = sf.read(stretched, dtype="float32")
    return np.asarray(out, dtype=np.float32)


def _write_mp3(audio: np.ndarray, output_path: Path) -> None:
    """Write WAV alongside, then encode to MP3 with ffmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path = output_path.with_suffix(".wav")
    sf.write(wav_path, audio, SAMPLE_RATE)

    # check=True: a failed encode must surface, not leave a truncated file.
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame", "-b:a", "128k", "-ar", str(SAMPLE_RATE),
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    wav_path.unlink()
