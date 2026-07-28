"""AI Podcast Generator - a stepped Streamlit workflow.

PDFs or an existing transcript go in; an MP3, five candidate titles, a show
description, cover art and a ZIP of all of it come out.

Speech and intro music are ElevenLabs. Script and metadata come from
the Anthropic API. Cover art comes from the zimage MCP server.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from dataclasses import replace
from pathlib import Path

import streamlit as st

from lib.bundle import build_show_notes, build_titles_text, build_zip, slugify
from lib.config import APP_DIR, load_settings
from lib.llm_client import LLMClient
from lib.models import (
    MAX_TARGET_MINUTES,
    MIN_TARGET_MINUTES,
    Gender,
    Persona,
    PodcastPackage,
    PodcastSpec,
    PodcastStyle,
    Transcript,
)
from lib.pdf_source import SourceDocument, combine_sources, extract_pdf
from lib.pipeline import THUMBNAIL_SIZE, THUMBNAIL_STEPS, produce_podcast
from lib.script_writer import (
    DEFAULT_DIRECTION,
    WORDS_PER_MINUTE,
    generate_thumbnail_prompt,
    generate_titles,
    generate_transcript,
    polish_transcript,
    reformat_transcript,
)
from lib.transcript_io import parse_transcript, render_transcript
from lib.tts_engine import SpeechClient
from lib.casting import name_for, new_seed, roles_for, voice_for
from lib.voices import ensure_catalog, is_live, label_for, voices_for
from lib.zimage_client import ZImageClient
from vt_banner import render_vt_banner

OUTPUT_ROOT = APP_DIR / "output"

STEPS: tuple[str, ...] = (
    "Connection",
    "Source material",
    "Cast & format",
    "Review script",
    "Generate",
    "Download",
)

PREVIEW_LINE = (
    "Here's the part that surprised me. The numbers didn't line up the way "
    "anyone expected, and that changed the whole picture."
)

# Names, voices and roles all rotate per session - see lib/casting.py. Only
# gender stays pinned to the slot, so a mixed cast stays mixed however the rest
# of it is dealt.
DEFAULT_GENDERS: tuple[Gender, ...] = (
    Gender.FEMALE,
    Gender.MALE,
    Gender.FEMALE,
)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


def init_state() -> None:
    defaults: dict[str, object] = {
        "step": 0,
        "episode_id": _new_episode_id(),
        "connection_ok": False,
        "source_mode": "PDF source material",
        "documents": (),
        "raw_transcript": "",
        "source_truncated": False,
        "spec": None,
        "transcript_text": "",
        "package": None,
        "chosen_title": "",
        "preview_audio": {},
        "cast_seed": new_seed(),
        "source_fingerprint": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _new_episode_id() -> str:
    """A sortable id for one episode's output directory.

    Timestamp first so `ls output/` reads chronologically. Milliseconds, not
    seconds: two topics started in the same second would otherwise sort by the
    random suffix, which is no order at all. The suffix stays purely to keep
    directories distinct if the clock ever does repeat.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return f"{stamp}-{uuid.uuid4().hex[:4]}"


def episode_output_dir() -> Path:
    """Where this episode's files go. Re-minted for each new topic."""
    return OUTPUT_ROOT / st.session_state["episode_id"]


def llm_client() -> LLMClient:
    return LLMClient(load_settings())


def zimage_client() -> ZImageClient:
    return ZImageClient(load_settings().zimage_url)


def speech_client() -> SpeechClient:
    return SpeechClient(load_settings().eleven_labs_api_key)


def music_cache_path() -> Path:
    """One intro clip for every episode, shared across sessions."""
    return APP_DIR / "assets" / "intro_music.wav"


def goto(step: int) -> None:
    st.session_state["step"] = step
    st.rerun()


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------


def inject_css() -> None:
    st.markdown(
        """
        <style>
          .block-container { padding-top: 3.5rem; max-width: 1100px; }
          .stepper { display: flex; gap: .4rem; margin: 0 0 1.6rem 0; flex-wrap: wrap; }
          .stepper .pip {
              flex: 1 1 0; min-width: 110px; padding: .5rem .7rem;
              border-radius: 6px; font-size: .8rem; font-weight: 600;
              border: 1px solid #d9d9de; color: #6b6b76; background: #fafafb;
              text-align: center; line-height: 1.25;
          }
          .stepper .pip.done { background: #f0e6eb; border-color: #c9a5b5; color: #861F41; }
          .stepper .pip.active { background: #861F41; border-color: #861F41; color: #fff; }
          .stepper .pip small { display: block; font-weight: 400; opacity: .75; font-size: .7rem; }
          textarea[aria-label="Script"] {
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important;
              font-size: .86rem !important; line-height: 1.55 !important;
          }
          .metric-row { display: flex; gap: 2rem; margin: .5rem 0 1.2rem 0; }
          .hint { color: #6b6b76; font-size: .85rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_stepper(current: int) -> None:
    pips = []
    for index, name in enumerate(STEPS):
        state = "active" if index == current else ("done" if index < current else "")
        pips.append(
            f'<div class="pip {state}"><small>Step {index}</small>{name}</div>'
        )
    st.markdown(f'<div class="stepper">{"".join(pips)}</div>', unsafe_allow_html=True)


def nav_buttons(
    back_to: int | None,
    next_label: str | None = None,
    next_to: int | None = None,
    next_disabled: bool = False,
    next_help: str | None = None,
) -> None:
    left, _, right = st.columns([1, 4, 1.4])
    if back_to is not None and left.button("Back", width="stretch"):
        goto(back_to)
    if next_label is not None and next_to is not None:
        if right.button(
            next_label,
            type="primary",
            width="stretch",
            disabled=next_disabled,
            help=next_help,
        ):
            goto(next_to)


# --------------------------------------------------------------------------
# Step 0 - connection
# --------------------------------------------------------------------------


def step_connection() -> None:
    st.subheader("Step 0 · Connection")
    st.caption(
        "Keys and model come from .env. Check that all three services respond "
        "before building an episode."
    )

    try:
        settings = load_settings()
    except ValueError as error:
        st.error(str(error))
        st.stop()

    st.markdown(
        f'<div class="hint">Model <code>{settings.model}</code> · '
        f'cover art <code>{settings.zimage_url}</code></div>',
        unsafe_allow_html=True,
    )

    st.write("")
    if st.button("Test connection", type="primary"):
        _run_connection_test(settings)

    if st.session_state["connection_ok"]:
        st.success("All three services responded.")

    st.write("")
    nav_buttons(
        back_to=None,
        next_label="Continue",
        next_to=1,
        next_disabled=not st.session_state["connection_ok"],
        next_help=None
        if st.session_state["connection_ok"]
        else "Run the connection test first.",
    )


def _run_connection_test(settings) -> None:
    ok = True

    with st.status("Checking services...", expanded=True) as status:
        st.write(f"Language model - {settings.model}")
        try:
            reply = LLMClient(settings).complete(
                system="You are a connection test.",
                user="Reply with exactly: OK",
                # No max_tokens: the client default is the model's full ceiling,
                # and a two-token answer is billed as two tokens. Low effort
                # keeps the test quick - it checks credentials, not capability.
                effort="low",
            )
            st.write(f"&nbsp;&nbsp;connected, replied {reply[:30]!r}")
        except Exception as error:  # surfaced verbatim; the UI is the only place to see it
            ok = False
            st.error(f"Language model failed: {type(error).__name__}: {error}")

        st.write("Cover art - zimage on ads2")
        try:
            info = zimage_client().server_info()
            structured = info.get("structuredContent", {})
            gpu = structured.get("gpu", "unknown GPU")
            free = structured.get("vram_free_gb", "?")
            st.write(f"&nbsp;&nbsp;connected, {gpu}, {free} GB VRAM free")
        except Exception as error:
            ok = False
            st.error(f"zimage failed: {type(error).__name__}: {error}")

        st.write("Speech - ElevenLabs")
        try:
            voices = ensure_catalog(settings.eleven_labs_api_key, refresh=True)
            source = "read from your account" if is_live() else "built-in fallback"
            st.write(f"&nbsp;&nbsp;{len(voices)} voices ({source})")
            st.write(f"&nbsp;&nbsp;connected, {speech_client().check()}")
        except Exception as error:
            ok = False
            st.error(f"ElevenLabs failed: {type(error).__name__}: {error}")

        status.update(
            label="All services responded" if ok else "Some services failed",
            state="complete" if ok else "error",
            expanded=not ok,
        )

    st.session_state["connection_ok"] = ok


# --------------------------------------------------------------------------
# Step 1 - source material
# --------------------------------------------------------------------------


def step_source() -> None:
    st.subheader("Step 1 · Source material")
    st.caption(
        "Upload the PDFs the episode should be built from, or bring an existing "
        "transcript to be reworked for this cast."
    )

    st.session_state["source_mode"] = st.radio(
        "What are you starting from?",
        ("PDF source material", "Existing transcript"),
        index=0 if st.session_state["source_mode"] == "PDF source material" else 1,
        horizontal=True,
    )

    if st.session_state["source_mode"] == "PDF source material":
        _pdf_input()
    else:
        _transcript_input()

    st.write("")
    nav_buttons(
        back_to=0,
        next_label="Continue",
        next_to=2,
        next_disabled=not _has_source(),
        next_help=None if _has_source() else "Add source material first.",
    )


def _has_source() -> bool:
    if st.session_state["source_mode"] == "PDF source material":
        return bool(st.session_state["documents"])
    return bool(st.session_state["raw_transcript"].strip())


def _pdf_input() -> None:
    uploads = st.file_uploader(
        "PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        help="Text-based PDFs only. Scanned documents would need OCR first.",
    )

    if uploads:
        documents: list[SourceDocument] = []
        failures: list[str] = []
        for upload in uploads:
            try:
                documents.append(extract_pdf(upload.name, upload.getvalue()))
            except ValueError as error:
                failures.append(str(error))

        st.session_state["documents"] = tuple(documents)
        for message in failures:
            st.error(message)

    documents = st.session_state["documents"]
    if not documents:
        return

    _, truncated = combine_sources(documents)
    st.session_state["source_truncated"] = truncated

    total_pages = sum(d.page_count for d in documents)
    total_chars = sum(d.char_count for d in documents)
    st.success(
        f"{len(documents)} document(s) · {total_pages} pages · "
        f"{total_chars:,} characters extracted"
    )
    if truncated:
        st.warning(
            "The combined material exceeds the input cap, so only the first "
            "240,000 characters will be sent to the model. Upload fewer or "
            "shorter documents if the later material matters."
        )

    with st.expander("Extracted text preview"):
        for document in documents:
            st.markdown(f"**{document.filename}** · {document.page_count} pages")
            st.text(document.text[:1200] + ("..." if document.char_count > 1200 else ""))


def _transcript_input() -> None:
    upload = st.file_uploader(
        "Transcript file", type=["txt", "md", "vtt", "srt"], accept_multiple_files=False
    )
    if upload is not None:
        st.session_state["raw_transcript"] = upload.getvalue().decode(
            "utf-8", errors="replace"
        )

    st.session_state["raw_transcript"] = st.text_area(
        "Transcript text",
        value=st.session_state["raw_transcript"],
        height=280,
        placeholder="Paste an existing transcript here, or upload a file above.",
        help=(
            "Any speaker labelling is fine - the model reassigns dialogue to the "
            "cast you define in the next step."
        ),
    )

    text = st.session_state["raw_transcript"].strip()
    if text:
        st.success(f"{len(text.split()):,} words · {len(text):,} characters")


# --------------------------------------------------------------------------
# Step 2 - cast and format
# --------------------------------------------------------------------------


def step_cast() -> None:
    st.subheader("Step 2 · Cast & format")
    st.caption(
        "Define who is speaking and how the material should be framed, then let "
        "the model write the script."
    )

    left, middle, right = st.columns(3)
    host_count = left.selectbox(
        "Number of hosts",
        (1, 2, 3),
        index=1,
        format_func=lambda n: f"{n} host" + ("" if n == 1 else "s"),
        help="One host produces a narrated explainer rather than a conversation.",
    )

    # A debate needs someone to disagree with, so it is only offered for 2+.
    style_options = (
        (PodcastStyle.OVERVIEW, PodcastStyle.DEEP_DIVE)
        if host_count == 1
        else (PodcastStyle.OVERVIEW, PodcastStyle.DEEP_DIVE, PodcastStyle.DEBATE)
    )
    style = middle.selectbox(
        "Style",
        style_options,
        index=1 if len(style_options) > 1 else 0,
        format_func=lambda s: s.value.title(),
        help="Debate needs at least two hosts."
        if host_count == 1
        else None,
    )
    target_minutes = right.slider(
        "Target length (minutes)",
        min_value=MIN_TARGET_MINUTES,
        max_value=MAX_TARGET_MINUTES,
        value=12,
        help=f"Roughly {WORDS_PER_MINUTE} spoken words per minute, so "
        f"{MAX_TARGET_MINUTES} minutes is about "
        f"{MAX_TARGET_MINUTES * WORDS_PER_MINUTE:,} words. Long episodes need "
        "source material with enough in it to sustain them.",
    )

    direction = st.text_area(
        "Direction for this episode",
        value=DEFAULT_DIRECTION,
        height=140,
        help="Tone, audience and stance - what the episode is for and how it "
        "should feel. This is the producer's brief and it leads the prompt. "
        "The episode's shape (cold open, central question, movements, closing "
        "callback), how the hosts argue with each other, and how the dialogue "
        "is paced all stay fixed in code whatever you put here.",
    )

    # Idempotent: normally a no-op because the connection test already fetched.
    # It matters when a session survives a code reload with connection_ok set.
    ensure_catalog(load_settings().eleven_labs_api_key)
    _start_new_episode()

    heading_col, shuffle_col = st.columns([3, 1])
    heading_col.markdown("##### Hosts")
    if shuffle_col.button("Shuffle cast", width="stretch"):
        _shuffle_cast()
    st.markdown(
        '<div class="hint">Names and voices are dealt fresh each session - '
        "shuffle to re-roll, or edit any of them directly. Voices are drawn at "
        "random from all ten of each gender - ElevenLabs publishes no quality "
        "grade, so none is treated as better than another. The pitch shown is "
        "measured from a real sample: pick two hosts far apart on it and they "
        "will be easy to tell apart.</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    personas = _persona_editors(host_count)

    st.write("")
    if st.button("Preview these voices"):
        _render_previews(personas)
    _show_previews(personas)

    st.write("")
    try:
        spec = PodcastSpec(
            personas=personas,
            style=style,
            direction=direction.strip() or DEFAULT_DIRECTION,
            target_minutes=target_minutes,
        )
    except ValueError as error:
        st.error(str(error))
        nav_buttons(back_to=1)
        return

    left_col, _, right_col = st.columns([1, 3, 2])
    if left_col.button("Back", width="stretch"):
        goto(1)
    if right_col.button("Write the script", type="primary", width="stretch"):
        _write_script(spec)


def _shuffle_cast() -> None:
    """Re-roll the rotating name, voice and role defaults.

    The seed is part of every name/voice/role widget key, so bumping it here
    means the editors rendered later in this same run build themselves fresh
    rather than replaying the values the user is trying to move away from.
    Previews are dropped with it - they are voiced by the voice that just
    changed.
    """
    st.session_state["cast_seed"] = new_seed()
    st.session_state["preview_audio"] = {}


def _source_fingerprint() -> str:
    """Identifies the source material currently loaded.

    Filenames and lengths rather than the text itself: enough to tell one upload
    from another, and it does not re-hash a megabyte of PDF on every rerun.
    """
    mode = st.session_state["source_mode"]
    if mode == "PDF source material":
        body = "|".join(
            f"{d.filename}:{d.char_count}" for d in st.session_state["documents"]
        )
    else:
        body = st.session_state["raw_transcript"].strip()
    return hashlib.sha1(f"{mode}\x00{body}".encode("utf-8")).hexdigest()[:16]


def _start_new_episode() -> None:
    """Re-roll the cast and open a fresh output directory for a new topic.

    A new topic gets new people and its own output directory. The check runs on
    arrival at Step 2 rather than
    when the source is edited, because the transcript box fires on every change
    and re-rolling there would reshuffle the cast underneath someone who is still
    pasting. Coming back from Step 3 leaves the fingerprint unchanged, so the
    cast a user has already tuned survives.

    The first visit records the fingerprint without re-rolling: the seed dealt at
    session start has not been used for anything else yet.
    """
    fingerprint = _source_fingerprint()
    if st.session_state["source_fingerprint"] == fingerprint:
        return
    if st.session_state["source_fingerprint"] is not None:
        _shuffle_cast()
        # A new directory rather than reusing the old one: episodes used to
        # share a per-session directory, so a second topic overwrote the first
        # one's podcast.mp3 and cover.png on disk.
        st.session_state["episode_id"] = _new_episode_id()
        # These name files in the directory just left behind.
        st.session_state["package"] = None
        st.session_state["chosen_title"] = ""
    st.session_state["source_fingerprint"] = fingerprint


def _remembered(field: str, index: int, stamp: str, fallback: str) -> str:
    """A cast edit the user made earlier in this deal, if there is one.

    Streamlit discards the state of any widget that was not rendered on a run,
    so walking to Step 3 and back reverts hand-edited names and roles to the
    dealt defaults - leaving a cast on screen that no longer matches the script
    just written from it. Mirroring each value into a plain (non-widget) key
    survives that, because plain keys are not garbage collected.

    The stamp carries the cast seed, so a re-deal - a new topic or Shuffle cast -
    lands on a fresh mirror key and the dealt default wins, which is what should
    happen. Only navigation restores an edit.
    """
    key = f"remembered_{field}_{index}_{stamp}"
    return st.session_state[key] if key in st.session_state else fallback


def _remember(field: str, index: int, stamp: str, value: str) -> None:
    st.session_state[f"remembered_{field}_{index}_{stamp}"] = value


def _persona_editors(host_count: int) -> tuple[Persona, ...]:
    personas: list[Persona] = []
    seed = int(st.session_state["cast_seed"])

    # Dealt for the whole cast at once rather than per slot, so the angles that
    # distinguish one expert from another come out distinct.
    dealt_roles = roles_for(host_count, seed)

    for index in range(host_count):
        default_gender = DEFAULT_GENDERS[index]
        default_role = dealt_roles[index]

        name_col, gender_col, voice_col = st.columns([1.1, 1, 1.6])

        # Gender is resolved before the name and voice because both defaults
        # are drawn from gender-specific pools. Writing to the columns out of
        # order is safe - layout follows the column, not the call.
        gender = gender_col.selectbox(
            "Gender",
            tuple(Gender),
            index=0 if default_gender is Gender.FEMALE else 1,
            format_func=lambda g: g.value.title(),
            key=f"gender_{index}",
        )

        # Both the gender and the seed ride in the widget keys: Streamlit
        # ignores `value`/`index` once a keyed widget exists, so flipping
        # gender or shuffling the cast would otherwise leave the old name and
        # voice sitting in a form that claims to have re-rolled them.
        stamp = f"{gender.value}_{seed}"
        default_name = _remembered(
            "name", index, stamp, name_for(gender, index, seed)
        )
        name = name_col.text_input(
            "Name", value=default_name, key=f"name_{index}_{stamp}",
            help="Used as the speaker label in the script.",
        )
        _remember("name", index, stamp, name)

        options = voices_for(gender)
        preferred = _remembered(
            "voice", index, stamp, voice_for(gender, index, seed)
        )
        option_ids = [v.voice_id for v in options]
        if preferred not in option_ids:  # remembered a voice this gender lacks
            preferred = voice_for(gender, index, seed)
        voice = voice_col.selectbox(
            "Voice",
            options,
            index=option_ids.index(preferred),
            format_func=label_for,
            key=f"voice_{index}_{stamp}",
        )
        _remember("voice", index, stamp, voice.voice_id)

        # The key carries the host count and the seed for the same reason the
        # name and voice keys do: Streamlit ignores `value` once a keyed widget
        # exists, so without them a solo host would keep a role that talks about
        # "the conversation", and Shuffle cast would not re-deal the roles.
        # Both of those strings go straight into the prompt.
        role_stamp = f"{host_count}_{seed}"
        role = st.text_input(
            "Role in the conversation" if host_count > 1 else "Role",
            value=_remembered("role", index, role_stamp, default_role),
            key=f"role_{index}_{role_stamp}",
            help="Shapes what this host talks about and how they behave. "
            "Every host is a subject-matter expert; the angle after the "
            "semicolon is what makes them a different one.",
        )
        _remember("role", index, role_stamp, role)
        st.write("")

        personas.append(
            Persona(
                name=name.strip() or default_name,
                gender=gender,
                voice_id=voice.voice_id,
                role=role.strip() or default_role,
            )
        )

    if len({p.voice_id for p in personas}) != len(personas):
        st.warning(
            "Two hosts share a voice, so listeners will not be able to tell them "
            "apart. Pick a different voice for one of them."
        )
    return tuple(personas)


def _render_previews(personas: tuple[Persona, ...]) -> None:
    from lib.tts_engine import synthesize_transcript
    from lib.models import Transcript as T, Turn

    previews: dict[str, bytes] = {}
    directory = episode_output_dir() / "previews"
    client = speech_client()
    with st.spinner("Rendering voice previews..."):
        for persona in personas:
            path = directory / f"{persona.voice_id}.mp3"
            # No music_path: the intro belongs on the episode, not on a
            # three-second sample of one host.
            synthesize_transcript(
                client=client,
                transcript=T(turns=(Turn(speaker=persona.name, text=PREVIEW_LINE),)),
                personas=(persona,),
                output_path=path,
            )
            previews[_preview_key(persona)] = path.read_bytes()
    st.session_state["preview_audio"] = previews


def _preview_key(persona: Persona) -> str:
    """Identifies a preview by what was actually rendered.

    Keyed on the voice as well as the name: keyed on the name alone, changing a
    host's voice and leaving the name alone would keep serving the old clip under
    a caption naming the new voice - the same stale-by-position mistake as the
    turn editor, one level up.
    """
    return f"{persona.name}\x00{persona.voice_id}"


def _show_previews(personas: tuple[Persona, ...]) -> None:
    previews = st.session_state["preview_audio"]
    if not previews:
        return
    columns = st.columns(len(personas))
    for column, persona in zip(columns, personas):
        key = _preview_key(persona)
        if key in previews:
            column.caption(f"{persona.name} · {persona.voice_id}")
            column.audio(previews[key], format="audio/mp3")


def _write_script(spec: PodcastSpec) -> None:
    st.session_state["spec"] = spec
    client = llm_client()

    placeholder = st.empty()

    def progress_for(stage: str):
        def on_progress(visible: int, reasoning: int) -> None:
            placeholder.markdown(
                f'<div class="hint">{stage}... {visible:,} characters of script, '
                f"{reasoning:,} characters of reasoning</div>",
                unsafe_allow_html=True,
            )

        return on_progress

    with st.spinner("The model is writing the script. This usually takes 20-90 seconds."):
        if st.session_state["source_mode"] == "PDF source material":
            source_text, _ = combine_sources(st.session_state["documents"])
            transcript = generate_transcript(
                client, spec, source_text, progress_for("Writing")
            )
        else:
            transcript = reformat_transcript(
                client, spec, st.session_state["raw_transcript"],
                progress_for("Writing"),
            )

    # Second pass over the finished draft, for how it sounds rather than what it
    # says. Separate call on purpose - see polish_transcript.
    with st.spinner("Second pass: smoothing the dialogue so it sounds spoken."):
        transcript = polish_transcript(
            client, spec, transcript, progress_for("Smoothing")
        )

    placeholder.empty()
    st.session_state["transcript_text"] = render_transcript(transcript)
    goto(3)


# --------------------------------------------------------------------------
# Step 3 - review
# --------------------------------------------------------------------------


def step_review() -> None:
    spec: PodcastSpec | None = st.session_state["spec"]
    if spec is None:
        st.warning("No script yet - go back and write one.")
        nav_buttons(back_to=2)
        return

    st.subheader("Step 3 · Review the script")
    st.caption(
        "Edit freely. Every line must start with a host name and a colon; hard "
        "wrapping inside a turn is fine."
    )

    editor_tab, turns_tab = st.tabs(["Script editor", "Turn by turn"])

    with editor_tab:
        st.session_state["transcript_text"] = st.text_area(
            "Script",
            value=st.session_state["transcript_text"],
            height=520,
            label_visibility="collapsed",
        )

    with turns_tab:
        _turn_editor(spec)

    transcript, error = _try_parse(spec)
    if error is not None:
        st.error(error)
    else:
        minutes, seconds = divmod(int(transcript.estimated_minutes * 60), 60)
        columns = st.columns(4)
        columns[0].metric("Turns", len(transcript.turns))
        columns[1].metric("Words", f"{transcript.word_count:,}")
        columns[2].metric("Estimated runtime", f"{minutes}m {seconds}s")
        columns[3].metric("Target", f"{spec.target_minutes}m")

        speakers = {t.speaker for t in transcript.turns}
        silent = [p.name for p in spec.personas if p.name not in speakers]
        if silent:
            st.warning(
                f"{', '.join(silent)} has no lines in the current script and "
                "will not be heard."
            )

    st.write("")
    left, middle, right = st.columns([1, 2.4, 1.6])
    if left.button("Back", width="stretch"):
        goto(2)
    if middle.button("Rewrite from scratch", width="stretch"):
        _write_script(spec)
    if right.button(
        "Generate podcast",
        type="primary",
        width="stretch",
        disabled=error is not None,
        help="Fix the script errors first." if error else None,
    ):
        goto(4)


def _try_parse(spec: PodcastSpec) -> tuple[Transcript | None, str | None]:
    try:
        return parse_transcript(st.session_state["transcript_text"], spec.personas), None
    except ValueError as error:
        return None, str(error)


def _script_nonce(spec: PodcastSpec) -> str:
    """Identity of the script currently under edit.

    The turn widgets have to be keyed by *what they contain*, not by their
    position. Streamlit ignores `value=`/`index=` once a keyed widget exists, so
    a key of `turn_text_3` means turn 3 of a freshly written script silently
    displays turn 3 of the previous one - and because that makes `changed` true,
    the "Apply turn edits" button appears and writes the stale text back under
    the new cast's names. Rewriting a script or changing the cast changes this
    hash, which retires the old widgets and builds new ones from the new text.
    """
    payload = st.session_state["transcript_text"] + "\x00" + "\x00".join(
        p.name for p in spec.personas
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _turn_editor(spec: PodcastSpec) -> None:
    transcript, error = _try_parse(spec)
    if transcript is None:
        st.info("Fix the script in the editor tab to use the turn-by-turn view.")
        st.error(error)
        return

    names = [p.name for p in spec.personas]
    nonce = _script_nonce(spec)
    edited: list[str] = []
    changed = False

    for index, turn in enumerate(transcript.turns):
        speaker_col, text_col = st.columns([1, 5])
        speaker = speaker_col.selectbox(
            "Speaker",
            names,
            index=names.index(turn.speaker),
            key=f"turn_speaker_{nonce}_{index}",
            label_visibility="collapsed" if index else "visible",
        )
        text = text_col.text_area(
            "Line",
            value=turn.text,
            key=f"turn_text_{nonce}_{index}",
            height=80,
            label_visibility="collapsed" if index else "visible",
        )
        if speaker != turn.speaker or text != turn.text:
            changed = True
        edited.append(f"{speaker}: {text.strip()}")

    if changed and st.button("Apply turn edits", type="primary"):
        st.session_state["transcript_text"] = "\n\n".join(edited)
        st.rerun()


# --------------------------------------------------------------------------
# Step 4 - generate
# --------------------------------------------------------------------------


def step_generate() -> None:
    spec: PodcastSpec = st.session_state["spec"]
    st.subheader("Step 4 · Generate")

    transcript, error = _try_parse(spec)
    if transcript is None:
        st.error(error)
        nav_buttons(back_to=3)
        return

    estimate = transcript.estimated_minutes
    st.caption(
        f"About to render roughly {estimate:.0f} minutes of audio, then write "
        "titles, a description and cover art. Expect one to three minutes."
    )

    if st.button("Start generation", type="primary"):
        _run_generation(spec, transcript)

    st.write("")
    nav_buttons(back_to=3)


def _run_generation(spec: PodcastSpec, transcript: Transcript) -> None:
    progress = st.progress(0.0, text="Starting...")

    def on_stage(label: str, fraction: float) -> None:
        progress.progress(min(max(fraction, 0.0), 1.0), text=label)

    package = produce_podcast(
        client=llm_client(),
        zimage=zimage_client(),
        speech=speech_client(),
        spec=spec,
        transcript=transcript,
        output_dir=episode_output_dir(),
        music_path=music_cache_path(),
        on_stage=on_stage,
    )

    st.session_state["package"] = package
    st.session_state["chosen_title"] = package.titles[0]
    goto(5)


# --------------------------------------------------------------------------
# Step 5 - download
# --------------------------------------------------------------------------


def _regenerate_titles() -> None:
    """Replace the five candidates with a fresh set from the same transcript."""
    package: PodcastPackage = st.session_state["package"]

    with st.spinner("Writing new titles..."):
        titles = generate_titles(llm_client(), package.transcript)

    st.session_state["package"] = replace(package, titles=titles)
    st.session_state["chosen_title"] = titles[0]
    st.rerun()


def _regenerate_cover_art() -> None:
    """Write a new art prompt from the selected title and render it again.

    The prompt is rewritten rather than reused: re-rendering the same prompt
    gives a near identical image, and the selected title may have changed since
    the first pass.
    """
    package: PodcastPackage = st.session_state["package"]
    title = st.session_state["chosen_title"] or package.titles[0]

    with st.spinner("Designing a new cover..."):
        prompt = generate_thumbnail_prompt(
            llm_client(), package.transcript, title, package.summary
        )

    with st.spinner("Rendering the image on zimage..."):
        path = zimage_client().generate_image(
            prompt=prompt,
            width=THUMBNAIL_SIZE,
            height=THUMBNAIL_SIZE,
            num_steps=THUMBNAIL_STEPS,
        ).save(episode_output_dir() / "cover.png")

    st.session_state["package"] = replace(
        package, thumbnail_path=path, thumbnail_prompt=prompt
    )
    st.rerun()


def step_download() -> None:
    package: PodcastPackage | None = st.session_state["package"]
    spec: PodcastSpec = st.session_state["spec"]

    if package is None:
        st.warning("Nothing generated yet.")
        nav_buttons(back_to=4)
        return

    st.subheader("Step 5 · Download")

    minutes, seconds = divmod(int(round(package.duration_seconds)), 60)
    st.success(f"Episode ready · {minutes}m {seconds}s")

    audio_bytes = package.audio_path.read_bytes()
    st.audio(audio_bytes, format="audio/mp3")

    art_col, meta_col = st.columns([1, 1.6])
    with art_col:
        # Bytes rather than the path: the file is overwritten in place on
        # regeneration, and a stale path would keep showing the old image.
        st.image(
            package.thumbnail_path.read_bytes(), caption="Cover art", width="stretch"
        )
        if st.button(
            "Regenerate cover art",
            width="stretch",
            help="Writes a fresh art prompt from the selected title and renders "
            "a new image. The old one is replaced.",
        ):
            _regenerate_cover_art()

    with meta_col:
        st.markdown("##### Title")
        st.session_state["chosen_title"] = st.radio(
            "Pick the title for the filenames and show notes",
            package.titles,
            index=package.titles.index(st.session_state["chosen_title"])
            if st.session_state["chosen_title"] in package.titles
            else 0,
            label_visibility="collapsed",
        )
        if st.button(
            "Regenerate titles",
            help="Asks the model for five new candidates. The current five are "
            "discarded.",
        ):
            _regenerate_titles()
        st.markdown("##### Description")
        st.write(package.summary)

    chosen_title = st.session_state["chosen_title"]
    stem = slugify(chosen_title)

    st.divider()
    st.markdown("##### Everything in one file")
    zip_bytes, zip_name = build_zip(package, spec, chosen_title)
    st.download_button(
        f"Download {zip_name}  ({len(zip_bytes) / 1_000_000:.1f} MB)",
        data=zip_bytes,
        file_name=zip_name,
        mime="application/zip",
        type="primary",
        width="stretch",
    )

    st.markdown("##### Individual files")
    row_one = st.columns(3)
    row_one[0].download_button(
        "Audio (.mp3)", audio_bytes, f"{stem}.mp3", "audio/mpeg", width="stretch"
    )
    row_one[1].download_button(
        "Cover art (.png)",
        package.thumbnail_path.read_bytes(),
        f"{stem}-cover.png",
        "image/png",
        width="stretch",
    )
    row_one[2].download_button(
        "Transcript (.txt)",
        render_transcript(package.transcript),
        f"{stem}-transcript.txt",
        "text/plain",
        width="stretch",
    )

    row_two = st.columns(3)
    row_two[0].download_button(
        "Titles (.txt)",
        build_titles_text(package.titles),
        f"{stem}-titles.txt",
        "text/plain",
        width="stretch",
    )
    row_two[1].download_button(
        "Summary (.txt)",
        package.summary,
        f"{stem}-summary.txt",
        "text/plain",
        width="stretch",
    )
    row_two[2].download_button(
        "Show notes (.txt)",
        build_show_notes(package, spec, chosen_title),
        f"{stem}-show-notes.txt",
        "text/plain",
        width="stretch",
    )

    with st.expander("All five titles"):
        st.text(build_titles_text(package.titles))
    with st.expander("Cover art prompt"):
        st.write(package.thumbnail_prompt)

    st.divider()
    left, _, right = st.columns([1, 3, 1.4])
    if left.button("Back", width="stretch"):
        goto(4)
    if right.button("Start a new episode", width="stretch"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# --------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="AI Podcast Generator", page_icon="🎙️", layout="wide"
    )
    render_vt_banner()
    inject_css()
    init_state()

    st.title("AI Podcast Generator")
    render_stepper(st.session_state["step"])

    steps = (
        step_connection,
        step_source,
        step_cast,
        step_review,
        step_generate,
        step_download,
    )
    steps[st.session_state["step"]]()


if __name__ == "__main__":
    main()
