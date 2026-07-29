# AI Podcast Generator

Turns PDFs (or an existing transcript) into a finished podcast episode: an MP3
with one to three distinct voices, five candidate titles, a Spotify-style
description, cover art, and a ZIP of all of it.

Runs at <http://localhost:8544> once started.

## Pipeline

| Stage | What does it |
|---|---|
| PDF text extraction | PyMuPDF, local |
| Script writing | Claude Opus 5 via the Anthropic API |
| Speech | ElevenLabs `eleven_v3` |
| Cover art | Z-Image Turbo via the `zimage` MCP server on ads2 |
| Intro music | ElevenLabs Music (`music_v2`), generated once and cached |
| Assembly | ffmpeg + Python `zipfile` |

## Setup

This app has its own conda env rather than sharing `genai` with the sibling
apps. It no longer needs a GPU, torch, or the numpy<2 pin — all three were
Kokoro's requirements, and speech is now an API call.

```bash
conda create -y -n podcastify python=3.11
conda activate podcastify
pip install -r requirements.txt
```

`ffmpeg` must be on PATH. `.env` must define `ANTHROPIC_API_KEY` and
`ELEVEN_LABS_API_KEY`; `ANTHROPIC_MODEL` defaults to `claude-opus-5` and `ZIMAGE_MCP_URL` to the ads2
server. A value in `.env` deliberately beats one already exported in the shell —
`ANTHROPIC_API_KEY` is a name other tooling sets globally, and authenticating
against a stray key looks like a billing mystery rather than a bug.

## Theme

The app is **light-only**, set in `.streamlit/config.toml`. Its own CSS hardcodes
light values — the stepper pips, the hint text, the VT maroon accent — so a dark
render puts dark-grey text on a near-white chip and the step indicator becomes
unreadable.

`base = "light"` alone is not enough to force it. The server sends the browser
three theme tables (`[theme]`, `[theme.light]`, `[theme.dark]`) and the *frontend*
picks between them from the viewer's system colour scheme or their choice in
Settings → Theme; there is no `theme.dark.base` to pin. So `[theme.dark]` mirrors
the light palette, which makes the dark branch render light too and the choice
unreachable however it is made.

## Running

```bash
./run_in_background.sh   # starts on port 8544, logs to streamlit.log
./stop_app.sh
```

It is also registered in `../start_all_ai_apps.sh`, which activates `genai`
globally — `run_in_background.sh` switches itself to `podcastify`.

## The six steps

0. **Connection** — check the LLM, zimage and ElevenLabs all respond. Keys and
   model come from `.env`; there is nothing to configure here.
1. **Source material** — upload PDFs, or paste/upload an existing transcript.
2. **Cast & format** — 1–3 hosts with name, gender, voice and role; style
   (overview / deep dive / debate); target length (5–60 minutes); extra
   direction (defaults to asking for a teasing rapport, some off-topic drift and
   occasional humour). Names, voices and roles are dealt fresh per session (see
   below) and re-rollable with **Shuffle cast**. Voices can be previewed first.
3. **Review script** — edit as free text, or turn-by-turn with speaker
   reassignment. Live turn/word/runtime counts.
4. **Generate** — audio, then titles, summary and cover art.
5. **Download** — pick a title, then take the ZIP or any individual file.

## Notes on the model

Scripts are written by **Claude Opus 5** (`ANTHROPIC_MODEL`, default
`claude-opus-5`). The app previously used the ARC OpenAI-compatible proxy; the
proxy's models were the ceiling on script quality, and its 8000-token
buffered-mode cap forced workarounds that are now gone.

Four things about this model that the code is built around:

- **No `temperature`.** Opus 5 rejects `temperature`, `top_p` and `top_k` with a
  400. The old code varied temperature per call (0.95 for titles, 0.85 for the
  script) to get variety; that is now asked for in the prompts instead, which is
  the supported lever. `lib/llm_client.py` exposes no sampling parameters at all.
- **Thinking is on by default and counts against `max_tokens`.** A budget that
  only fits the answer yields a script truncated mid-sentence. Rather than
  hand-tuning headroom, every call now asks for the model's full 128k output
  ceiling (`MAX_OUTPUT_TOKENS` in `llm_client.py`) and lets the prompt's word
  target set the length — `max_tokens` is a cap, not a reservation, and unused
  budget is not billed. Thinking is requested as `display: "summarized"` purely
  so the UI's progress counter moves — Opus can think for a while before writing
  a character, and a frozen zero reads as a hung app.
- **A refusal is a 200, not an exception.** Safety classifiers can decline a
  request; it arrives as `stop_reason="refusal"` with empty or half-written
  content. Reading the text without checking would silently produce a truncated
  script, so `_text_or_raise` checks the stop reason first. Source PDFs are
  arbitrary in a teaching context, so requests also pass `fallbacks="default"` —
  the API re-runs a refusal on its recommended fallback model inside the same
  call, and only a refusal from *both* surfaces as an error.
- **Titles use structured outputs.** `complete_json` passes a JSON schema that
  the API enforces, replacing the previous fenced-block-and-regex scraping.

## Notes on episode length

Target length runs 5–60 minutes, enforced in `PodcastSpec` as well as the slider
so the bound holds for any caller. Nothing in the app caps output below the
model's own ceiling: the previous 8k/24k/32k/64k token constants are gone, and
`combine_sources` now accepts 1M characters of source text (it was 240k, sized
for the ARC proxy's shared 131k window, not for Claude's 1M-token input).

**Claude overshoots the word target, and the overshoot grows with length.**
Measured against a 23-page review article, two hosts, deep dive:

| Target | Target words | Actual words | Overshoot | Wall clock |
|---|---|---|---|---|
| 5 min | 750 | 847 | 113% | 42 s |
| 60 min | 9,000 | 10,935 | 122% | 6 min 26 s |

The original prompt said "getting close matters more than hitting it exactly,"
which Opus 5 reads literally — that wording alone produced **13,349 words (148%,
about 89 minutes)** for a 60-minute request. Restating the count as a budget with
a ±5% tolerance, and saying that running long is as much a failure as running
short, cut it to 122%. It is closer, not exact: **ask for 60 and expect roughly
73 minutes.** Size the request accordingly if the runtime is a hard constraint.

## Notes on the voices

Speech is the ElevenLabs API using `eleven_v3`, the model ElevenLabs recommends
for multi-speaker dialogue. Turns are synthesised individually (each needs its own
voice) and stitched with a gap that is longer on a speaker change than on the
same speaker continuing.

**The catalog is read from your account at runtime.** With `voices_read`
granted on the API key, `GET /v2/voices` is the source of truth: it returns the
voices the account actually has, with ElevenLabs' own labels for gender, accent,
age and use case. Cloned and Voice Library additions appear automatically, and
nothing goes stale when ElevenLabs retires a default.

That replaced a hand-maintained static list, which was wrong in both directions:
three of its entries (Rachel, Aria, Charlotte) are not in this account at all —
legacy globals that still synthesise but are not in the library — and it was
missing five that are, including a professional voice the account owner had
picked. `_FALLBACK_VOICES` survives only so a network blip during Step 2 does not
leave the picker empty.

**One trap the API sets.** The voices endpoint and the TTS endpoint disagree: a
voice copied from the Voice Library keeps being listed after its owner disables
it, and only fails when you try to speak with it (`403 voice_disabled`). Left
alone that is a picker entry that kills a render halfway through — exactly what
happened on the first live test. `lib/voices.py` drops anything whose
`sharing.status` is `copied_disabled`. Run `python scripts/check_voices.py` to
list the catalog and confirm every entry still synthesises.

**Gender labels are wider for choosing than for dealing.** ElevenLabs labels a
voice `female`, `male`, `neutral`, or leaves it unlabelled. The picker offers a
host every voice matching their gender *plus* the neutral and unlabelled ones, so
nothing the account owner added is unreachable. The random cast draw uses an
exact label match only — dealing an unlabelled voice to a host the script calls
"she" is a mismatch nobody asked for. Currently: 8 offered / 7 dealt for female,
14 / 13 for male.

Verified across 4,000 seeds: every castable voice is reachable in every slot,
evenly spread, three hosts never collide on a voice, and every dealt voice
carries the matching gender label.

Two constraints worth knowing:

- **`previous_text` / `next_text` are rejected by `eleven_v3`** with a 400
  (`unsupported_model`). They would give cross-turn prosody continuity, and the
  code still sends them for models that accept them, but v3's expressiveness is
  worth more here than the continuity.
- **`pcm_44100` returns 403** on this tier. The app requests `pcm_24000`, which
  is raw PCM — so there is no per-turn MP3 decode, and 24 kHz is well above what
  speech needs.

**Text to Dialogue, not per-turn synthesis.** A whole exchange goes to
`/v1/text-to-dialogue` in one request, so the model sees the turns either side of
each line and matches prosody and turn-taking across them. Per-turn synthesis
structurally cannot do that — every line was previously generated blind to the
conversation it sits in, which is what makes stitched dialogue sound like two
people reading at each other.

Turns are batched at boundaries up to the documented 2,000-character ceiling: a
5-minute episode becomes about **3 requests instead of 33**, with prosody
continuous inside each. Only the seams between batches get a gap; inside one the
model places its own transitions. Batches still render in parallel, and a failure
falls back to per-turn synthesis so an episode stays renderable.

**Sounding human.** From ElevenLabs' guidance, after rounds where the voices
came out robotic:

- **`stability` is Creative (0.0).** It has walked down the whole scale. Robust
  (1.0) is described by ElevenLabs as *"less responsive to directional
  prompts... similar to v2"* — it ignores audio tags and bought turn-to-turn
  evenness by making the read wooden (the evenness it was chasing was really an
  8.5 dB loudness spread that `_match_loudness` handles directly). Natural
  (0.5) still read as lifeless. Creative is the mode the v3 prompting guide
  pairs with audio tags, at the cost of an occasional hallucinated noise a
  regenerate fixes.
- **Stability is the only setting v3 honours.** Per the voice-settings docs,
  similarity, style, speed and speaker boost are each *"not available for the
  Eleven v3 model"* — the API accepts and ignores them — so v3 requests send
  `stability` alone. Per-line delivery is steered entirely by audio tags in
  the text, which is why the performance pass below exists.
- **Audio tags are open vocabulary**, filtered by shape rather than whitelist:
  one to three lowercase words in square brackets reaches the synthesiser
  (`[laughs]`, `[skeptical]`, `[jumping in]`, `[long pause]`); citations and
  prose stage directions are stripped. v3 *performs* tags rather than reading
  them aloud, and punctuation is performance too — ellipses render as
  hesitation, CAPITALS as emphasis, a dash ending a turn as a real cut-off.

**Speed is applied after synthesis, not asked of the model.** `voice_settings.speed`
is accepted by both endpoints and **does nothing on eleven_v3** — measured, three
samples each on identical text: 0.7 → 6.88/6.80/6.24s, 1.0 → 6.40/7.44/6.16s,
1.2 → 6.64/6.56/6.80s, entirely generation noise where 0.7 against 1.2 should
differ by nearly a factor of two. Text to Dialogue has no speed control at all.
`_apply_tempo` uses ffmpeg's `atempo`, which resamples without shifting pitch and
is applied to speech only, so the intro music keeps its own tempo.

*(An earlier version of this file claimed the pace change made episodes 8.4%
faster. The gap tightening was measured and real; the speed half was derived by
assuming `speed` scaled duration linearly, which it does not.)*

Batches are rendered four at a time; order is preserved regardless of completion
order.

## Notes on the rotating cast

Three things are dealt fresh each session so repeat episodes don't all open with
the same two people in the same chairs: **names** (a pool of 10 per gender),
**voices** (any gender-labelled voice on the account), and the **expert angle**
each one brings. Only gender stays pinned to the slot, so a mixed cast stays
mixed however the rest is dealt. **Shuffle cast** re-rolls
all three; every field is still editable by hand.

The cast is re-dealt when the **source material changes** — a new topic gets new
people. The check runs on arrival at Step 2, not while the source is being
edited: the transcript box fires on every change, and re-rolling there would
reshuffle the cast underneath someone still pasting. Walking to Step 3 and back
leaves it alone, and hand-edited names, voices and roles survive that trip
(they are mirrored outside widget state, because Streamlit discards the state of
any widget it did not render on a run). A genuine topic change overrides them.

All three derive from one per-session `cast_seed` rather than being drawn per
call. Streamlit reruns the whole script on every widget interaction, so an
unseeded `random.choice` would reshuffle the form underneath whoever was filling
it in. The seed also rides in each widget key, because Streamlit ignores `value`
once a keyed widget exists — without that, shuffling would leave the old values
sitting in a form claiming to have re-rolled them.

**Every host is a subject-matter expert.** What rotates is the *angle* each one
brings — historical precedent, systems thinking, shipped-it experience, watching
the numbers, and so on — dealt distinctly so a three-host cast is three different
experts rather than three copies of one.

### What lives in the prompt vs what the producer owns

The **direction box in Step 2 is the brief**: what the episode is for and how it
should feel. It leads both prompts, and everything else serves it. It defaults to
an exploration that teaches the material — what the thing is, how it works, what
it is good at, where it falls down, who should care — delivered in a fun,
interactive way.

Fixed in code, whatever the brief says: the opening beats, the length budget, the
speech/formatting rules, the cast brief and `_INTERACTION_RULE`. Those are
structure and host behaviour, not approach.

Also fixed, as guardrails rather than approach:

- **Ground everything in the material.** Name the actual components, steps and
  trade-offs. If a stretch of dialogue would leave the listener knowing nothing
  new, it does not belong in the episode.
- **Stay on the subject, not on the source.** The material is the source, not the
  topic. Never discuss how the document is written, organised or worded, or
  whether it contradicts itself. Where it genuinely is unclear, say in one clause
  what is known and move on.

That second guardrail exists because of a real failure: fed a framework's
documentation, the episode became a running complaint about the docs conflicting
rather than an explanation of the framework. `_INTERACTION_RULE` was partly to
blame — it said the talk *"matters more than covering the material"*, which is
now inverted to *"this is HOW the teaching happens, never a substitute for it"*,
and challenges are explicitly scoped to substance rather than presentation.

Measured on documentation containing deliberate contradictions: source/doc
mentions fell to **11% of turns** while **61%** carried framework substance, and
the episode covered what it is, how to use it, strengths, limits and who should
care — closing on *"The primitives are good. The edges are yours."*

### The smoothing and performance passes

Every script goes through the LLM **three times**. The first pass writes it; a
second pass (`polish_transcript`) sees only the finished draft and rewrites it
to sound spoken rather than written; a third (`perform_transcript`) marks up
delivery — audio tags, emphasis capitals, hesitation ellipses — without
touching a word of the dialogue. The performance pass is modelled on the
prompt behind ElevenLabs' own "Enhance" button, which they publish in the v3
prompting guide, and runs last so it annotates the words that will actually be
spoken. Both the fresh-script and reformat paths run all three, and so does
**Rewrite from scratch** — they all funnel through `_write_script`.

It is a separate call on purpose. The writing prompt is already carrying the
cast, the style, the length budget, the interaction rules and the source
material; asking it to also self-edit for AI tells in the same breath gets the
tells acknowledged and left in. A pass that sees only the draft has one job.

The tell list is adapted from Wikipedia's *Signs of AI writing* — significance
inflation, the AI vocabulary, superficial "-ing" tails, negative parallelism,
rule of three, copula avoidance, synonym cycling, false ranges, filler and
hedging, generic uplift, and uniform sentence length, which is the loudest tell
of all when spoken.

What it must **not** touch is spelled out just as explicitly: speaker
assignments, claims, numbers, the order ideas arrive in, the word budget, and
above all the disagreements — *you are smoothing the prose, not the conflict*.
One format-specific carve-out: a dash at the **end** of a turn marks an
interruption and stays; mid-sentence dramatic dashes are a tell and go.

On a deliberately tell-laden draft, 18 hits went to 0 at identical word count,
and the pass strengthened rather than softened the argument — the original had a
host ignore an objection and change topic, the rewrite has her answer it:

> **before** — *This study serves as a testament to the transformative potential of automated chemistry, marking a pivotal moment in the evolving landscape…*
> **after** — *Okay, so, automated chemistry. This study's the one that makes me think the whole thing actually works.*

On an already-clean draft it does less, as it should: 745 → 723 words, same 33
turns, same speakers, pushback count unchanged, sentences shorter and more
numerous. It roughly doubles script-writing time and LLM cost.

### The opening

`_opening_brief()` builds the opening beats and varies with cast size, because
"introduce the others" is nonsense for a solo host. The order is: **hook, then
introductions, then substance.**

The hook comes first deliberately. A host cannot say why a topic resonates with
them before the listener knows what the topic is, and "hi, I'm Maya" lands on
nobody who has not yet been given a reason to care.

The beats: hook → a bridge line naming the subject and handing into the
introductions (*"Today we're getting into X, but let's start by meeting
everyone"*) → that host introduces themselves by first name with a line on how
they come at the material → the others, **first name only** → each answers with
a hello and one specific sentence on why it lands for them. One may skip the
reason, since all of them delivering the same beat reads like a form.

**Naming the subject is a hard requirement**, in the always-on guardrails rather
than the opening brief so it holds for the whole episode. If the subject has a
proper name — product, system, library, paper, method — the script must say it
out loud in the first few sentences. *"A framework for building agents"* is not
naming it; *"Microsoft's Agent Framework"* is.

This exists because of a self-inflicted bug. The guardrail against reviewing the
source document read *"never the name of the document it came from"*, and with a
source file called `agent_framework_docs.md` that suppressed the product name
too — an entire episode about Microsoft's Agent Framework that never once said
so, only "agent frameworks" as a generic plural. The ban now says explicitly that
naming the thing the source describes is *required*; it is discussing the
document as an artefact that is not.

The bridge line is specified by shape, not wording — the model is told to write
it fresh each time, because a verbatim stock sentence across every episode is
exactly what makes a podcast sound automated. It names the *subject*, never the
source document.

Banned in the opening: show names, surnames, job titles read like a conference
badge, "welcome back", "thanks for having me". The whole thing is capped at about
forty seconds — a doorway, not a segment.

### Two failures, and the rule that threads between them

`_INTERACTION_RULE` in `script_writer.py` is the heart of the script, and it was
arrived at by hitting a failure on each side:

- **One host in an interviewer seat**, asking questions it already knew the
  answer to so the other could explain. Every exchange a setup and a payoff.
- **Two experts who never engage**, taking turns delivering correct statements —
  which is what you get from simply banning the first failure. Two people
  reading a script at each other.

Questions were never the problem; *interviewer* questions were. The rule now asks
for argument between peers and for the understanding to arrive through it: call
BS often, argue about whether things **matter** and not only about what is true,
ask real questions (from disagreement or genuine uncertainty, never as a cue),
always engage the actual objection rather than letting it drop, and let someone
end the episode holding a different view than they started with.

Measured across all three versions, same source article, two hosts, deep dive:

| | questions | pushback | concessions | interruptions |
|---|---|---|---|---|
| expert + prober | 17% | 0% | 0 | 0 |
| two flat SMEs | 0% | 2% | 0 | 0 |
| **interacting SMEs** | **7%** | **15%** | **1** | **2** |

Argument costs words, so the LENGTH section tells the model to drop topics rather
than flatten the exchange to fit more in. **The word budget still governs total
runtime** — this style covers less ground per minute, it does not by itself
produce a longer episode. Raise the target-length slider for that.

## Notes on the cover art

The art prompt is written by Claude from the episode's **title, summary and
transcript**, then rendered by zimage. The summary matters: it is already a
40-70 word statement of what the episode is about, which is exactly the
high-level read a cover needs — the transcript alone buries the subject under an
hour of dialogue.

**This deliberately departs from the original brief's "abstract graphics."** The
prompt used to demand `ABSTRACT` art and appended *"Abstract non-representational
artwork"* to every prompt, which worked directly against a cover that shows what
the episode is about. It now asks for stylised, graphic treatment of the actual
subject — recognisable in silhouette, but not a literal photograph. The bans
survive unchanged and are enforced twice, in the prompt and in zimage's
`NEGATIVE_PROMPT`: no text or lettering, no people or faces, and none of the
podcast-cliche microphones, headphones or waveforms.

Two renders from the same code, different subjects: a self-driving-lab episode
produced a robot arm lowering a vial past a spectrometer, with an unresolved
molecular blur on the glass above it; a shipping-tariff episode produced an
aerial container port with gantry cranes and queued ships. Both legible at a
glance, neither carrying text or figures.

## Notes on the intro music

Each episode opens with the same few seconds of instrumental music, generated
once by ElevenLabs Music and cached at `assets/intro_music.wav` (24 kHz mono, so
loading it at render time costs a file read). The bed fades in over 2s, runs 7s
total, fades out over the last 3s, and the speech enters 2s before it ends so it
ducks away under the first line rather than stopping dead.

Music is **best-effort by design**: the brief called it non-critical, so a
failure to generate or load it is swallowed and the episode ships without an
intro rather than failing a render that is otherwise complete. Delete the cached
file to get a different clip.


## Why not podcastfy

The brief suggested [podcastfy](https://github.com/souzatharsis/podcastfy). It
was not used: its `text_to_speech.py` validates that `<Person1>`/`<Person2>` tags
strictly alternate and raises otherwise, so it cannot produce a single-host
explainer or a three-way conversation. It also owns no voices of its own — its
provider adapters are 30-line shims over ElevenLabs, OpenAI, Edge and Google
Cloud TTS — so adopting it would not have answered the voice question, only
wrapped it, at the cost of a langchain + vertexai + litellm dependency tree.

## Layout

```
podcast_app.py        Streamlit wizard (all six steps)
.streamlit/
  config.toml         forces the light theme
lib/
  config.py           .env loading
  models.py           typed domain model
  voices.py           ElevenLabs voice catalog (pitch measured, not copied)
  casting.py          rotating default host names and voice picks
  pdf_source.py       PyMuPDF extraction
  llm_client.py       streaming Anthropic client
  script_writer.py    all prompts: script, titles, summary, art
  transcript_io.py    parse/render the editable script format
  tts_engine.py       ElevenLabs speech + intro music + ffmpeg assembly
  zimage_client.py    hand-rolled MCP JSON-RPC client
  pipeline.py         stage orchestration behind Step 4
  bundle.py           ZIP packaging
scripts/
  check_voices.py     lists the live catalog and verifies every voice synthesises
assets/
  intro_music.wav     generated on first render, then reused (gitignored)
```

Generated episodes land in `output/<episode-id>/` and are gitignored. The id is
`YYYYmmdd-HHMMSS-mmm-xxxx`, so `ls output/` reads chronologically.

**A new directory is minted per topic**, on the same trigger as the cast re-deal.
Episodes previously shared one per-session directory, so starting a second topic
in the same browser tab silently overwrote the first one's `podcast.mp3` and
`cover.png`. Reruns and Step 2/3 round trips keep the directory; only a genuine
source change opens a new one.
