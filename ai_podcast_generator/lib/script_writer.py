"""LLM prompts that turn source material into a script and its metadata.

All prompt construction lives here so the wording can be tuned without touching
the pipeline.

The prompt is assembled from independent blocks, each answering one question,
and they are composed only at the top level in `build_transcript_prompt` and
`build_reformat_prompt`. Nothing is spliced into the middle of anything else:

    DIRECTION    - the producer's brief. The one part the user owns.
    ARC          - what shape the episode takes, beat by beat.
    TEACHING     - what the listener must come away with.
    DYNAMICS     - how the hosts treat each other.
    SPEECH       - how the words sit in the mouth: interruptions, fragments.
    SYNTH        - formatting, which is absolute because a TTS engine reads it.

The split between SPEECH and SYNTH matters. SYNTH is machine-readable structure
and breaking it breaks the render. SPEECH is craft. They used to live under one
heading, which taught the model that "vary your sentence length" carried the
same weight as "never emit markdown".

The ARC, DYNAMICS and SPEECH blocks are modelled on a specific 50-minute episode
whose transcript is checked in at reference/crewai_episode_transcript.txt. Where
a rule below states a number, the number was measured from that transcript
rather than guessed - see the comments on `_SPEECH_PATTERNS`.
"""

from __future__ import annotations

from .llm_client import LLMClient, ProgressCallback
from .models import WORDS_PER_MINUTE, PodcastSpec, PodcastStyle, Transcript
from .transcript_io import parse_transcript, render_transcript

# The producer's brief: what this episode is FOR and how it should feel. This is
# the default contents of the direction box in Step 2, not a constant the code
# depends on - the user is expected to rewrite it.
#
# It is deliberately about tone, audience and stance only. Structure, host
# behaviour and speech craft stay in code below, so that a user who types "make
# it funnier" does not accidentally delete the episode's shape.
DEFAULT_DIRECTION = (
    "Make this a real exploration, not a summary. The listener should finish "
    "able to explain the thing to somebody else: what it is, how it actually "
    "works, what it costs, where it breaks, and who should walk away from it. "
    "Keep the energy up - curious, quick, a little irreverent, and willing to "
    "say plainly when something is overhyped. Assume a smart listener who is "
    "short on time and allergic to marketing language."
)

# There is deliberately no per-call token budget here. Every call falls through
# to the client default - the model's own 128k ceiling - because max_tokens is a
# cap rather than a reservation and unused budget is not billed. Length is set by
# the prompt's word target, not by starving the budget.

# Enforced by the API rather than requested in the prompt, so a reply is either
# this shape or an error - never prose wrapped around an object.
_TITLES_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "titles": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["titles"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# ARC - the shape of the episode
# ---------------------------------------------------------------------------
#
# The reference episode spends its first two and a half minutes on a broken arm
# and an X-ray machine without once naming its subject, then breaks the analogy
# ("suddenly that X-ray machine is completely broken") and lands on the tension
# the whole episode is about. It closes by returning to the same X-ray. That
# open-tension-question-movements-callback shape is what this block encodes.
#
# The counted movement rule exists because "structure it well" produced episodes
# that were one long undifferentiated conversation. A number the model can check
# itself against lands where a preference did not.

_ARC_CORE = """\
EPISODE ARCHITECTURE - the episode has a shape, and the shape is not "talk
about the material until the words run out". Build it in these beats, in order.

BEAT 1 - COLD OPEN ON SOMETHING THAT IS NOT THE SUBJECT.
Open on a concrete, physical, everyday scene that any listener already
understands: a broken bone and an X-ray, a bad hire, a kitchen that runs out of
one ingredient, a queue at a border. Do NOT name the subject yet. Do NOT say
what the episode is about. Build the scene between the hosts across several
short turns - one sets it up, the other adds to it, they enjoy it a little.
It should feel like they wandered in mid-conversation.

BEAT 2 - BREAK IT.
Turn the scene against itself. The everyday case is clean and the case this
episode cares about is not, and that failure IS the tension the episode exists
to explore. Name the tension in one or two sentences, plainly. This is the hinge
of the whole opening: "and then you step into X, and suddenly that machine is
completely broken."

BEAT 3 - NAME THE SUBJECT AND THE MISSION.
Now say what this is. Use the subject's own proper name out loud - the product,
the paper, the system, the method, the organisation - with whatever version,
date or scope the material specifies. Say in one line what the episode is going
to do, and say what it is NOT going to do: it is not a feature list read aloud,
not a summary, not an audio textbook. A listener who joined at this exact
sentence should be able to tell somebody else what they are listening to.

BEAT 4 - POSE THE CENTRAL QUESTION.
State, out loud, the one question this episode exists to answer. It must be a
genuine puzzle drawn from the material - something that looks contradictory
until you understand the thing properly. "Why do smart, well-funded teams build
a prototype that works beautifully and then hit a wall in production?" Everything
after this beat is the answer arriving in instalments. Refer back to it when a
movement lands a piece of the answer.

BEAT 5 - THE MOVEMENTS.
{movements}

Each movement has its own internal shape. It opens with one host raising
something, it gets challenged or complicated by the other, and it closes with
the two of them somewhere they were not at the start. A movement that begins and
ends in the same place is filler - cut it and give its minutes to another.

Move between movements with a spoken hinge, not a silence: a host asks the
question the last movement raised, or says what they still do not understand.
Never a section announcement.

BEAT 6 - SYNTHESIS.
Pull it together. What does all of this actually mean for the listener? No new
facts here - this beat earns its place by connecting things already said. One
compact exchange, not a recap of every movement.

BEAT 7 - THE CLOSING THOUGHT, WITH A CALLBACK.
End on a real thought that goes slightly beyond the material: what this implies,
what might make it irrelevant, what nobody has settled yet. Then return
explicitly to the image from BEAT 1 and read it differently in light of
everything since. The callback is not optional - it is what makes the episode
feel like one thing rather than a list.

Close on that thought. Never thank the listener for subscribing, never plug the
show, never promise what is coming next week."""

_MOVEMENT_MENU = """\
The body of the episode is a sequence of MOVEMENTS. Aim for one movement per
five minutes or so of running time, and never fewer than three. Choose the ones
the material actually supports, in an order where each earns the next - do not
force a movement the source cannot fill, and do not cover everything at half
depth. Good movements to draw from:

- GROUNDING: what this thing is, where it came from, how big it actually is.
  Any claim of scale, adoption or performance gets challenged on the spot - name
  the specific weasel word and say what it is hiding.
- CORRECTION: a widespread belief about the subject that is out of date or
  simply wrong. Say what people think, then dismantle it with specifics.
- MECHANISM: how the thing actually works, worked through properly. This is the
  centre of gravity of most episodes and usually deserves the most minutes.
- THE TURN: what it costs, where it breaks, who gets burned. Hard numbers.
  This beat is mandatory in every episode - an episode with no turn is an
  advertisement.
- ALTERNATIVES: what else you would use instead, and the real basis for choosing
  between them. Compare on specifics, not on vibes.
- PLAYBOOK: what the listener should actually do on Monday morning. A short
  ordered set of decisions, spoken naturally - "first ask yourself X; if the
  answer is yes, stop, you are done."
"""


def _arc_brief(spec: PodcastSpec) -> str:
    """The arc, with the movement count scaled to the running time."""
    suggested = max(3, round(spec.target_minutes / 5))
    movements = (
        f"{_MOVEMENT_MENU}\n"
        f"At {spec.target_minutes} minutes, plan for roughly {suggested} "
        "movements. Fewer, deeper movements always beat more, shallower ones."
    )
    return _ARC_CORE.format(movements=movements)


# ---------------------------------------------------------------------------
# TEACHING - what the listener must leave with
# ---------------------------------------------------------------------------

_TEACHING_CONTRACT = """\
THE TEACHING CONTRACT - the entertainment is how the teaching happens, never a
substitute for it. Every stretch of dialogue must leave the listener knowing
something they did not know.

USE THE NAME. Say the subject's proper name out loud and keep saying it. Do not
slide into "this framework", "the paper", "the system" for the rest of the hour.

TEACH THE MECHANISM, NOT THE SUMMARY. Name the actual components, steps and
trade-offs. When something matters, have a host ask how it actually works - and
then answer it concretely enough that the listener could repeat the explanation.
"It improves accuracy" is not teaching. "That string gets injected into the
system prompt on every call, so it shifts the token probabilities" is.

NUMBERS OUT LOUD, IN CONTEXT. Use the real figures from the material and give
them a scale the listener can feel: not "significantly slower" but "450
milliseconds instead of 120, and it compounds - four seconds before the user
sees anything". Never invent a number. Where the material gives none and one is
needed, have a host say so.

DEFINE JARGON THE MOMENT IT LANDS. The first time a term appears that a smart
outsider would not know, one host stops and defines it in a sentence, explicitly
for the listener. Then move on - do not turn it into a lesson.

REACH FOR A PHYSICAL ANALOGY IN EVERY MOVEMENT. Concrete, everyday, and load
bearing: an org chart, a stage manager, a rumour crossing a school cafeteria.
The analogy must do explanatory work, not decorate a point already made. Push it
until it breaks, then say where it breaks.

STAY ON THE SUBJECT, NOT ON THE SOURCE. The material is where the facts come
from, not what the episode is about. Never discuss how the document is written,
organised or worded, how clear or complete it is, or whether it contradicts
itself. Where the source genuinely does not settle something, say in one clause
what is known and what is not, then get back to the substance. Complaining about
documentation is not an episode.

GROUNDING. Everything said must be supported by the material. Do not invent
studies, numbers, quotes or events. Where the material is silent, say so plainly
rather than filling the gap."""


# ---------------------------------------------------------------------------
# DYNAMICS - how the hosts treat each other
# ---------------------------------------------------------------------------
#
# Two failure modes sit either side of this and the wording has to thread
# between them:
#
#   - One host in an interviewer seat asking questions it already knows the
#     answer to so the other can explain. Every exchange becomes a setup and a
#     payoff, and it is unlistenable.
#   - Two experts who never engage, taking turns delivering correct statements.
#     Equally dull, and the usual result of simply banning the first.
#
# Questions are not the problem; interviewer questions are. What is wanted is
# argument between peers, with the understanding arriving through it.

_DYNAMICS_SHARED = """\
HOW THE HOSTS TREAT EACH OTHER - this is HOW the teaching happens.

They are peers. Everyone in the cast has read the material and has their own
read on it. Nobody is there to be taught, and nobody is the interviewer.

They do lean differently, and that is what makes them distinguishable. One tends
to reach for the framing, the analogy and the listener's objection; another tends
to hold the detail, the mechanism and the numbers. These are tendencies, not job
titles - they swap, and each has to carry real substance across the episode.

THE MOVES THEY MAKE - use all of these, repeatedly, across the episode:

- CHALLENGE THE SUBSTANCE. "That is not what the material says." "I don't buy
  it." "Where's the number for that?" When a claim is soft, name the exact word
  doing the work: "'in some form' is carrying an enormous amount of weight in
  that sentence." Pushback lands throughout, not once an episode.
- VOICE THE LISTENER'S OBJECTION. A host says out loud what the listener is
  thinking - "I can hear people listening to this thinking half a second is
  nothing, who cares" - and then it gets answered properly. This is the single
  most valuable move in the format and it should appear several times.
- ASK FOR THE MECHANISM. A real question, asked because the answer is worth
  having on the record: "can you explain what is actually happening there, for
  anyone who isn't deep in this?" That is legitimate. NEVER ask a question you
  already know the answer to purely so the other can perform an explanation.
- CONCEDE SPECIFICALLY. "That's fair." "You've hit the exact trap." A concession
  names what it is conceding. Never let a good objection just drop.
- ARGUE ABOUT WHETHER IT MATTERS, not only about what is true. Is this
  important? Worth what it costs? Would anyone actually use it? Disagreement
  about significance is more interesting than disagreement about fact.
- COMPLETE EACH OTHER'S THOUGHT. One starts the sentence, the other lands it,
  the first confirms and extends. "You're working off an old map." / "Exactly.
  A map that doesn't match the territory any more."
- ECHO AND AMPLIFY. Pick up the other's word and raise it. "It's a chasm." / "A
  massive chasm." / "A chasm that is swallowing whole teams right now."
- CHANGE YOUR MIND. At least one host should end the episode holding a view they
  did not hold at the start, and it should be visible when it happens."""

_DYNAMICS_SOLO = """\
SINGLE HOST. {name} is an expert in this material and presents it alone,
speaking directly to the listener. It is a narrated explainer, not a
conversation: there is no co-host, no interview, and nobody to address but the
listener.

The architecture, the teaching contract and the speech patterns below all still
apply - the cold open, the central question, the movements, the callback, the
short fragments and self-corrections. What changes is that the pushback is
internal. {name} raises the objection themselves and answers it: "and I know
what you're thinking - half a second, who cares. Here's why it matters."
Voice the listener's scepticism out loud and take it seriously.

Keep it warm and direct - talking to one person, not lecturing a hall. Every
line must begin with '{name}: '."""

# The countable rules below replace softer phrasings that were ignored outright:
# a 694-turn script once came back with zero consecutive same-speaker turns and
# perfect A-B-A-B alternation. Rates measured from the reference transcript are
# in the SPEECH block; the ban here is on the pattern.
_DYNAMICS_MULTI = """

NEVER ALTERNATE STRICTLY. Rigid ping-pong is the most common failure in written
dialogue and a listener hears it immediately. Vary who is driving - the same
host should sometimes carry two or three exchanges in a row, and it is fine for
a host to sit out a short stretch entirely."""

_DYNAMICS_THREE = """

WITH THREE HOSTS: tell them apart by the angle each brings, not by how much each
knows. What one of them chooses to raise should be recognisably theirs. Do not
rotate them in a fixed order. Real three-way conversation is uneven - just make
sure all three carry real weight across the episode, and nobody is reduced to
agreeing."""


def _dynamics_brief(spec: PodcastSpec) -> str:
    if len(spec.personas) == 1:
        return _DYNAMICS_SOLO.format(name=spec.personas[0].name)
    block = _DYNAMICS_SHARED + _DYNAMICS_MULTI
    if len(spec.personas) >= 3:
        block += _DYNAMICS_THREE
    return block


# ---------------------------------------------------------------------------
# SPEECH - how the words sit in the mouth
# ---------------------------------------------------------------------------
#
# Every number in this block was measured from the reference transcript
# (373 turns, 9,238 words across 50 minutes):
#
#   mean 24.8 words per turn, median 24
#   1-5 words    16% of turns
#   6-15 words   19%
#   16-30 words  29%
#   31-60 words  33%
#   61+ words     3%
#   same-speaker transitions: 62 of 372, ~1 in 6
#
# The single loudest difference between that episode and a model-written script
# is the 16% of turns under six words. Those are what make it sound like two
# people rather than two essays.

_SPEECH_PATTERNS = """\
SPEECH PATTERNS - what makes this sound spoken rather than written. These are
measured from a real episode, so treat the proportions as targets you can check
yourself against.

TURN LENGTH IS LUMPY. Across the whole script, aim for roughly:
- one turn in six under six words
- one turn in five between six and fifteen words
- about a third between sixteen and thirty
- about a third between thirty-one and sixty
- a bare handful over sixty

Short turns are not filler. They are the load-bearing structure of a real
conversation, and a script without them reads as two people taking turns
lecturing. "Ouch." "Right, they're guessing." "Just a small project." "That's
devastating." "Wait, really?"

SAME SPEAKER, TWICE IN A ROW. About one transition in six carries the same
speaker again - reacting and then continuing, correcting themselves, or adding
the thing they forgot. This is correct and wanted, not a mistake to tidy up.

BACK-CHANNEL. Real listeners make noise while the other talks: "Right." "Oh,
sure." "Totally." "Yeah, exactly." "Oh, that old chestnut." Sprinkle these
through, especially inside a long explanation, so it does not become a
monologue. Do not let one become a verbal tic that both hosts repeat.

INTERRUPTIONS. A dash at the END of a turn means the other host cut in and the
sentence never finished. Use it where somebody genuinely could not wait. Do not
use dashes mid-sentence for effect - that is a writing tic, not a speech one.

IMPRECISION. Let people be as loose as they are out loud: "the thing where", "I
mean", "sort of", a sentence that restarts partway through, a self-correction
mid-turn. Sparingly, and not as a mannerism shared by every host.

THINKING NOISES. An "um" or an "uh" belongs where a speaker is genuinely
working something out - reaching for a number, changing tack mid-sentence,
answering a question they had not seen coming. A handful per episode, at real
decision points. Never as seasoning sprinkled over every turn, and never at
the start of consecutive turns.

REPEAT WORDS. Speakers say the same word again. Do not cycle through platform,
system, framework, solution to avoid repeating yourself - that is a writing
habit and it makes a listener think four different things are being discussed.

TALK TO THE LISTENER. Address them directly and often: "if you're listening to
this right now", "and I want to be clear with you up front", "for anyone who
isn't deep in this". The listener is the third person in the room.

CONTRACTIONS EVERYWHERE. "It's", "they're", "that'd", "we've", "you'd".

VARY SENTENCE LENGTH HARD, inside turns as well as across them. Follow a long
winding sentence with three words.

NEVER begin consecutive turns with the same word.

DO NOT USE these phrases: "dive into", "unpack", "at the end of the day", "it's
not just X, it's Y", "game changer", "buckle up", "let that sink in"."""


# ---------------------------------------------------------------------------
# SYNTH - formatting, absolute
# ---------------------------------------------------------------------------

_SYNTH_RULES = """\
OUTPUT FORMAT - absolute, because this text is fed straight to a speech
synthesiser that reads every character literally:
- Output ONLY lines of the form "Name: spoken words". Nothing else.
- Put a blank line between turns.
- No markdown. No asterisks, headers, bullet points or numbered lists.
- No section labels like "INTRO", "MOVEMENT 2" or "BEAT 1". The architecture
  shapes the script; it never appears in it.
- AUDIO TAGS. The synthesiser PERFORMS bracketed tags rather than reading them
  aloud, and they are the main thing standing between this script and a flat,
  even read. Use them freely - several per movement, anywhere a real person's
  delivery would shift. Write them lowercase, one or two words, in square
  brackets, either at the start of a turn or immediately before the words they
  colour.
    reactions: [laughs] [chuckles] [sighs] [exhales] [scoffs] [groans] [gasps]
    feeling:   [excited] [amused] [curious] [skeptical] [surprised] [thoughtful]
               [hesitant] [impressed] [frustrated] [sarcastic] [deadpan] [wry]
    delivery:  [whispers] [softly] [slowly] [quickly] [emphatic] [rushed]
               [drawn out] [short pause] [long pause]
  Other tags of the same shape are fine - one to three lowercase words. Never
  two tags in a row, and do not tag every turn; a tag means something only if
  the untagged lines around it are neutral. A tag's effect fades after a
  sentence or two, so restate the mood partway through a long turn that has to
  hold it.
- PUNCTUATION IS PERFORMANCE, not grammar. The synthesiser plays an ellipsis
  as a real hesitation ("It... well, it might work"), a word in CAPITALS as
  spoken emphasis ("that took HOURS"), and a dash ending a turn as a genuine
  cut-off. Use all three where the delivery calls for them, and sparingly - a
  script where every line shouts emphasises nothing.
- INTERRUPTIONS, PERFORMED. When one host cuts in, end the interrupted turn on
  a dash mid-thought and open the interrupting turn with [jumping in] or
  [interrupting], so it renders as a barge-in rather than a polite hand-off.
- Do NOT use round brackets or asterisks for delivery. "(pause)", "(laughs)"
  and "*sighs*" are read out word for word. Square brackets only.
- Spell out symbols and abbreviations as they should be said: "percent" not "%",
  "and" not "&", "figure two" not "Fig. 2".
- Never speak a citation, reference marker, or URL."""


# ---------------------------------------------------------------------------
# Style, cast and assembly
# ---------------------------------------------------------------------------

_STYLE_BRIEF: dict[PodcastStyle, str] = {
    PodcastStyle.OVERVIEW: (
        "OVERVIEW. Favour breadth: more movements, each moving briskly, so the "
        "listener finishes knowing what the whole of the material covers and "
        "why it matters. The turn and the playbook still happen - breadth is "
        "not an excuse to skip the costs or the advice."
    ),
    PodcastStyle.DEEP_DIVE: (
        "DEEP DIVE. Favour depth: take the two or three most consequential "
        "ideas and work them properly - mechanism, evidence, implications, what "
        "remains unresolved. Fewer movements, each fully explored. It is fine "
        "to leave whole sections of the material unmentioned."
    ),
    PodcastStyle.DEBATE: (
        "DEBATE. The central question in BEAT 4 is one the hosts genuinely "
        "disagree about, and they hold opposed positions drawn from real "
        "tensions in the material. They press each other for evidence, concede "
        "specific points and refuse to concede others. Do not resolve into "
        "bland agreement - end with the disagreement narrowed but still real."
    ),
}


def _persona_block(spec: PodcastSpec) -> str:
    return "\n".join(
        f"- {p.name} ({p.gender.value}) - {p.role}" for p in spec.personas
    )


def _intro_beat(spec: PodcastSpec) -> str:
    """Where the hellos go.

    Deliberately after the subject is named. A host cannot say why a topic
    resonates with them before the listener knows what the topic is, and
    "hi, I'm Maya" lands on nobody who has not been given a reason to care.
    """
    if len(spec.personas) == 1:
        return (
            f"INTRODUCTIONS: fold into BEAT 3. Once the subject is named, "
            f"{spec.personas[0].name} says hello and introduces themselves by "
            "FIRST NAME ONLY, with a line on how they come at this material. "
            "Two sentences at most, then straight on."
        )
    opener, rest = spec.personas[0], spec.personas[1:]
    others = ", ".join(p.name for p in rest)
    hand_off = f"then hands to {others}" if len(rest) == 1 else (
        f"then hands to the others: {others}"
    )
    return (
        f"INTRODUCTIONS: fold into BEAT 3, never before it. After the subject "
        f"is named, {opener.name} introduces themselves by FIRST NAME ONLY with "
        f"a few words on how they come at this material, {hand_off}. "
        "Each answers with a quick hello and, in one "
        "sentence, why this particular topic interests them - something "
        "specific to the material or their own work, never \"great to be here\". "
        "One of them may just say hello and skip the reason; that reads more "
        "naturally than all of them delivering the same beat. Let the hellos "
        "overlap and interrupt the way real ones do. No surnames, no job titles "
        "read out like a conference badge, no naming the show, no \"welcome "
        "back\". The whole thing is under about thirty seconds of speech."
    )


def _system_prompt() -> str:
    return (
        "You are an experienced podcast writer. You write scripts that sound "
        "like people actually talking, not like an article read aloud. You "
        "build episodes with a deliberate shape, you make people argue, and "
        "you follow formatting instructions exactly."
    )


def build_transcript_prompt(spec: PodcastSpec, source_text: str) -> str:
    target_words = spec.target_minutes * WORDS_PER_MINUTE
    return f"""\
Write a podcast script based entirely on the source material at the end of this
message.

THE BRIEF FROM THE PRODUCER - this sets what the episode is for and how it
should feel. Everything below serves it:
{spec.direction}

CAST:
{_persona_block(spec)}

STYLE:
{_STYLE_BRIEF[spec.style]}

{_arc_brief(spec)}

{_intro_beat(spec)}

{_TEACHING_CONTRACT}

{_dynamics_brief(spec)}

{_SPEECH_PATTERNS}

LENGTH:
Write {target_words} words, plus or minus five percent. At speaking pace that is
about {spec.target_minutes} minutes, which is what the listener was promised, so
treat it as a budget rather than a target to beat. Running long is as much a
failure as running short.

Argument costs words: a point that is raised, challenged, defended and revised
takes three times the space of a point simply stated. That trade is worth
making. If the material has more in it than the budget allows, drop whole
movements - never flatten the exchanges into a sequence of statements to fit
more in.

{_SYNTH_RULES}

SOURCE MATERIAL:
{source_text}
"""


def build_reformat_prompt(spec: PodcastSpec, raw_transcript: str) -> str:
    target_words = spec.target_minutes * WORDS_PER_MINUTE
    return f"""\
Below is an existing transcript. Rebuild it as a podcast script for this cast,
preserving its substance while giving it the shape and sound of a real episode.

THE BRIEF FROM THE PRODUCER - this sets what the episode is for and how it
should feel. Everything below serves it:
{spec.direction}

CAST:
{_persona_block(spec)}

STYLE:
{_STYLE_BRIEF[spec.style]}

WHAT TO CHANGE:
- Reassign all dialogue to the cast above, whatever speaker names the original
  used. If the original has more speakers than the cast, merge them sensibly; if
  fewer, distribute the material so every host has real substance to carry.
- Impose the architecture below even if the original has no shape at all. The
  original's running order is not sacred: reorder freely to build the arc, and
  invent the cold open and the callback, which the original almost certainly
  lacks.
- Rewrite written-register phrasing into spoken register.
- Remove filler that exists only on the page: headers, timestamps, speaker
  numbering, transcription artefacts like "[inaudible]" or verbatim "um".
- Drop any "welcome back to the show" opening the original had.

WHAT TO PRESERVE:
- Every substantive claim, number and example. Do not add facts that are not in
  the original, and do not drop findings to save space.

{_arc_brief(spec)}

{_intro_beat(spec)}

{_TEACHING_CONTRACT}

{_dynamics_brief(spec)}

{_SPEECH_PATTERNS}

LENGTH:
Write {target_words} words, plus or minus five percent. Treat it as a budget
rather than a target to beat. If the original is much longer, tighten by cutting
repetition rather than by dropping content.

{_SYNTH_RULES}

EXISTING TRANSCRIPT:
{raw_transcript}
"""


def generate_transcript(
    client: LLMClient,
    spec: PodcastSpec,
    source_text: str,
    on_progress: ProgressCallback | None = None,
) -> Transcript:
    """Write a fresh script from source material."""
    raw = client.complete(
        system=_system_prompt(),
        user=build_transcript_prompt(spec, source_text),
        on_progress=on_progress,
    )
    return parse_transcript(raw, spec.personas)


def reformat_transcript(
    client: LLMClient,
    spec: PodcastSpec,
    raw_transcript: str,
    on_progress: ProgressCallback | None = None,
) -> Transcript:
    """Rework a user-supplied transcript onto this cast."""
    raw = client.complete(
        system=_system_prompt(),
        user=build_reformat_prompt(spec, raw_transcript),
        on_progress=on_progress,
    )
    return parse_transcript(raw, spec.personas)


# ---------------------------------------------------------------------------
# Polish pass
# ---------------------------------------------------------------------------
#
# Deliberately narrow. The writing pass owns structure, substance, dynamics and
# speech craft; this pass sees only the finished draft and fixes prose tells.
# It does not restate the speech rules - repeating them here is what previously
# had the two passes arguing with each other over the same ground.

_AI_TELLS = """\
Tells to hunt for, drawn from the catalogue of AI writing patterns. Most are
invisible on the page and obvious in the ear:

- Significance inflation: "marks a pivotal moment", "a testament to",
  "underscores the importance of", "represents a shift", "the evolving
  landscape of". Nobody says this out loud. Cut it or replace it with the
  concrete thing.
- The AI vocabulary: delve, crucial, pivotal, underscore, showcase, foster,
  garner, intricate, tapestry, landscape (figurative), testament, align with,
  enhance, robust, leverage, key (as an adjective), additionally.
- Superficial "-ing" tails bolted on to fake depth: "..., highlighting the
  tension", "..., reflecting a broader shift", "..., ensuring accuracy". Delete
  them; the sentence was finished without them.
- Negative parallelism: "it's not just X, it's Y", "not only... but also".
- The rule of three. Three parallel items, three adjectives, three examples.
  Real speech gives one, or two, or five, and rarely balances them.
- Copula avoidance: "serves as", "stands as", "functions as", "represents".
  A person says "is".
- Synonym cycling: the same thing called a platform, then a system, then a
  framework, purely to avoid repeating a word. Repeat the word.
- False ranges: "from X to Y" where X and Y are not ends of any real scale.
- Filler and hedging: "in order to", "it is important to note that", "at this
  point in time". Say the thing.
- Generic uplift: "the future looks bright", "an exciting step forward", any
  closing sentiment that could be pasted onto a different episode.
- Every sentence the same length. This is the loudest tell of all when spoken."""


def build_polish_prompt(spec: PodcastSpec, draft: str) -> str:
    target_words = spec.target_minutes * WORDS_PER_MINUTE
    return f"""\
Below is a draft podcast script. Rewrite it so it sounds like people talking
rather than a language model writing dialogue. This is a second pass over
finished work: the structure and the substance are settled and you are fixing
how it sounds, line by line.

{_AI_TELLS}

WHAT TO PRESERVE EXACTLY - the draft is right about all of this:
- Every speaker name, and who says what. Do not reassign a line.
- Every claim, number, name, study and finding. Add no facts, drop none.
- The order the ideas arrive in, the shape of the episode, and where it ends up.
  In particular, leave the opening image and the closing callback to it intact.
- The disagreements. Where a host pushes back, challenges, calls something out
  or concedes, that exchange stays and stays sharp. You are smoothing the PROSE,
  not the conflict.
- The very short turns. A two-word reaction is doing structural work; do not
  merge it into its neighbour or expand it into a sentence.
- Roughly {target_words} words, plus or minus five percent. A rewrite, not a
  trim and not an expansion.

WHAT TO CHANGE:
- Break the rhythm. Real speech is lumpy: a nine-word sentence next to a
  forty-word one, a fragment, a sentence that restarts partway through.
- Let people be imprecise the way they are out loud - "the thing where", "sort
  of", "I mean", a self-correction mid-turn. Sparingly, and never as a tic
  repeated by every host.
- Contractions everywhere, unless the stress is the point.
- Cut anything a person would not say aloud but a writer would type.
- Keep opinions and let them be uneven. A host may be unfair, may overstate,
  may trail off.
- A dash at the END of a turn marks one host cutting another off. Those stay.
  Dashes used mid-sentence for dramatic effect are an AI tell - replace them
  with a comma or a full stop.

{_SYNTH_RULES}

Reply with the rewritten script only - no preamble, no notes, no explanation of
what you changed.

DRAFT SCRIPT:
{draft}
"""


def polish_transcript(
    client: LLMClient,
    spec: PodcastSpec,
    transcript: Transcript,
    on_progress: ProgressCallback | None = None,
) -> Transcript:
    """Second pass: rewrite the draft so it sounds spoken rather than written.

    Kept as a separate call rather than folded into the writing prompt because
    the first pass is already carrying the cast, the arc, the length budget, the
    teaching contract, the dynamics and the source material. Asking it to also
    self-edit for AI tells in the same breath gets the tells acknowledged and
    left in. A pass that sees only the finished draft has one job.

    Falls back to the draft if the rewrite comes back unparseable - a script
    that sounds slightly written beats no script at all.
    """
    draft = render_transcript(transcript)
    raw = client.complete(
        system="You are a script doctor for audio. You rewrite dialogue so it "
        "sounds like it was spoken, not written. You never change the facts, "
        "you never soften a disagreement, and you never tidy away a short line.",
        user=build_polish_prompt(spec, draft),
        on_progress=on_progress,
    )
    try:
        return parse_transcript(raw, spec.personas)
    except ValueError:
        return transcript


# ---------------------------------------------------------------------------
# Performance pass
# ---------------------------------------------------------------------------
#
# Modelled on the prompt behind ElevenLabs' own "Enhance" button, which they
# publish in the v3 prompting guide. The writing and polish passes produce the
# words; this pass directs how the words are delivered - audio tags, emphasis
# capitals, hesitation ellipses - and is forbidden from touching the words
# themselves. ElevenLabs run exactly this step as a separate LLM call, and for
# the same reason the polish pass is separate here: a pass that owns delivery
# alone applies it line by line, where a writing pass asked to also perform
# tags a few early turns and forgets.
#
# eleven_v3 has no per-turn settings; tags in the text are the ONLY per-line
# delivery control, which is what makes this pass the difference between a
# flat read and a performed one.

_PERFORMANCE_RULES = """\
WHAT YOU MAY DO:
- Insert audio tags: square brackets, one to three lowercase words, each
  describing something you can HEAR in a voice. Place a tag immediately before
  the words it colours ("[skeptical] That seems high.") or immediately after
  the sentence it reacts to ("Two million dollars. [laughs]").
    reactions: [laughs] [chuckles] [sighs] [exhales] [scoffs] [gasps] [groans]
    feeling:   [excited] [amused] [curious] [skeptical] [surprised]
               [thoughtful] [hesitant] [impressed] [frustrated] [sarcastic]
               [deadpan] [warmly] [nervous] [dismissive]
    delivery:  [whispers] [softly] [slowly] [quickly] [emphatic] [rushed]
               [drawn out] [short pause] [long pause]
  Other tags of the same shape are welcome. The draft may already carry some
  tags: keep, move or replace them as the performance calls for.
- Add emphasis by putting a word that is already there in CAPITALS. Add
  hesitation or weight with an ellipsis (...). Change a full stop to a
  question mark or an exclamation mark where the line is clearly asked or
  exclaimed.
- Where a turn ends on a dash (an interruption), open the next turn with
  [jumping in] or [interrupting] so it plays as a barge-in.

WHAT YOU MUST NOT DO:
- Never add, remove, reorder or respell a word. The dialogue is final; you are
  marking up its delivery, not editing it.
- Never tag anything that is not a sound a voice makes. No [standing], no
  [grinning], no [music], no prose directions like [pauses for a long moment].
- Never put two tags in a row.
- Never use round brackets or asterisks; the synthesiser reads those aloud.

DENSITY AND JUDGEMENT:
- Mark the peaks and the genuine reactions, and leave the level ground alone.
  Most lines carry no tag at all; a tag only reads as a real reaction when the
  lines around it are neutral. Aimed at a whole episode, that is a few tags
  per minute of speech, not per line.
- A tag's effect fades after a sentence or two. In a long turn that has to
  hold a mood, restate the tag partway through.
- Range matters: the same [chuckles] forty times reads as a tic. Draw on the
  whole emotional vocabulary, matched to what each line is actually doing.
- Keep each host's temperament in mind: a laugh belongs to the line that
  earned it, not to a quota."""


def build_performance_prompt(draft: str) -> str:
    return f"""\
Below is a finished podcast script, ready for a speech synthesiser that
PERFORMS square-bracketed audio tags rather than reading them aloud, and that
plays capitals as emphasis and ellipses as hesitation. Your job is the
performance direction: mark HOW every line is delivered without changing what
is said.

{_PERFORMANCE_RULES}

Keep every speaker name and the "Name: spoken words" line format exactly as
given, with a blank line between turns.

Reply with the annotated script only - no preamble, no notes.

SCRIPT:
{draft}
"""


def perform_transcript(
    client: LLMClient,
    spec: PodcastSpec,
    transcript: Transcript,
    on_progress: ProgressCallback | None = None,
) -> Transcript:
    """Third pass: annotate delivery - audio tags, emphasis, hesitation.

    Falls back to the unannotated script if the markup comes back unparseable,
    for the same reason polish does: a flat read beats no episode.
    """
    draft = render_transcript(transcript)
    raw = client.complete(
        system="You are a performance director marking up a script for an "
        "expressive speech synthesiser. You direct delivery - tags, emphasis, "
        "pauses - and you never rewrite the dialogue itself.",
        user=build_performance_prompt(draft),
        on_progress=on_progress,
    )
    try:
        return parse_transcript(raw, spec.personas)
    except ValueError:
        return transcript


# ---------------------------------------------------------------------------
# Episode metadata
# ---------------------------------------------------------------------------


def generate_titles(
    client: LLMClient, transcript: Transcript, count: int = 5
) -> tuple[str, ...]:
    """Catchy episode titles."""
    body = _transcript_excerpt(transcript)
    result = client.complete_json(
        system="You write podcast episode titles that make people click without "
        "feeling tricked.",
        user=f"""\
Write {count} candidate titles for this podcast episode.

Each title must:
- be under 70 characters
- be concrete about what the episode actually covers, not vague intrigue
- avoid colons splitting a clever phrase from an explanation, which is the house
  style of every other podcast
- avoid the words "unlocking", "unpacking", "deep dive", "secrets", "revealed",
  "ultimate guide", and "you need to know"

Make them genuinely different from each other in angle - not {count} rewordings
of one idea. Vary the form: some a statement, some a question, some a fragment.
Reach for the unobvious angle rather than the first one that fits; two titles
that could be swapped without anyone noticing means one of them is wasted.

EPISODE TRANSCRIPT:
{body}
""",
        schema=_TITLES_SCHEMA,
    )
    titles = result.get("titles")
    if not isinstance(titles, list) or not titles:
        raise ValueError(f"Expected a non-empty 'titles' list, got: {result!r}")

    cleaned = tuple(str(t).strip() for t in titles if str(t).strip())
    if not cleaned:
        raise ValueError(f"All returned titles were blank: {result!r}")
    return cleaned[:count]


def generate_summary(client: LLMClient, transcript: Transcript) -> str:
    """Short show-notes blurb in the register Spotify or Apple Podcasts uses."""
    body = _transcript_excerpt(transcript)
    return client.complete(
        system="You write podcast show notes. You reply with the blurb text only, "
        "with no heading, no label, and no quotation marks around it.",
        user=f"""\
Write the episode description for this podcast, as it would appear in Spotify or
Apple Podcasts.

Requirements:
- Two or three sentences, 40 to 70 words total.
- Say what the episode actually covers, concretely enough that someone can decide
  whether to listen.
- Present tense, second or third person. No "in this episode we will".
- No hashtags, no emoji, no call to action, no host names unless they carry
  meaning for a stranger.
- Do not use the words "unpack", "dive", "delve", or "explore".

EPISODE TRANSCRIPT:
{body}
""",
    ).strip().strip('"')


def generate_thumbnail_prompt(
    client: LLMClient, transcript: Transcript, title: str, summary: str
) -> str:
    """Image prompt for zimage: cover art that reads as being about the episode.

    Takes the summary as well as the transcript because the summary is already a
    40-70 word statement of what the episode is about - exactly the high-level
    read the cover needs. The transcript alone buries that under an hour of
    dialogue.
    """
    body = _transcript_excerpt(transcript)
    prompt = client.complete(
        system="You write prompts for a text-to-image model. You reply with the "
        "prompt text only - no preamble, no explanation, no quotation marks.",
        user=f"""\
Write a single image-generation prompt for the cover art of this podcast episode.

THE COVER MUST BE ABOUT THIS EPISODE. Someone scrolling a podcast feed, who has
not heard it, should be able to glance at the cover and tell roughly what subject
it covers - chemistry, transport, language, economics, whatever it happens to be.
Build the image out of what this episode is actually about: the objects,
materials, structures, processes, instruments or spaces the material describes.
Work at the level of the whole subject, not one detail from one paragraph.

Style: stylised and graphic rather than photographic. Simplified forms, strong
shapes, a deliberate palette, a sense of composition and depth, a definite
lighting quality, and a material or texture. Subject matter should be
recognisable in silhouette even though the treatment is stylised - a picture of
nothing in particular is a failure, and so is a literal photograph of laboratory
equipment.

Hard constraints, because the image model renders these badly or they break the
brief:
- No text, letters, numbers, words, or logos anywhere in the image.
- No people, faces, hands, or figures.
- No microphones, headphones, podcast studios, or audio waveforms - these are
  the cliche of the genre.

Write one flowing paragraph of 40 to 70 words. Describe only the image.

Commit to one specific visual idea rather than hedging across several. Avoid the
default palette of teal and orange gradients on dark backgrounds.

EPISODE TITLE: {title}

WHAT THE EPISODE IS ABOUT:
{summary}

EPISODE TRANSCRIPT:
{body}
""",
    ).strip().strip('"')

    # Belt and braces: zimage gets the negative prompt too, but restating the
    # ban in the positive prompt measurably reduces stray lettering.
    return f"{prompt} Stylised graphic cover art, no text, no lettering, no people."


def _transcript_excerpt(transcript: Transcript, max_chars: int = 200_000) -> str:
    """Transcript text for metadata prompts, trimmed from the middle if long.

    The cap is a backstop against pathological input, not a working limit: at
    200k characters it is roughly four times the longest episode this app can
    produce (60 minutes is about 54k characters), so a real transcript reaches
    the titles and summary calls whole.

    If it does trip, keeping the head and tail preserves the framing and the
    conclusion, which is what titles and summaries are actually drawn from.
    """
    text = render_transcript(transcript)
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n[... middle of episode omitted ...]\n\n{text[-half:]}"
