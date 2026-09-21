import json
import os
import random
import re

import anthropic
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROVIDER_ARC = os.getenv("OPEN_AI_PROVIDER_NAME", "OpenAI")
PROVIDER_CLAUDE = "Claude Sonnet"
PROVIDERS = [PROVIDER_ARC, PROVIDER_CLAUDE]

DEFAULT_INSTRUCTIONS = (
    "Generate plausible distractor answers that are similar to the correct answer "
    "in style, length, and domain terminology, but are clearly wrong upon careful "
    "consideration. Avoid obviously wrong or absurd distractors."
)

_SYSTEM_PROMPT = (
    "You are an expert quiz question generator. "
    "You must ONLY generate questions based on the material provided by the user. "
    "Do not use any knowledge from your training data. "
    "Every question, correct answer, and distractor must be directly supported by the provided text. "
    "If the material does not contain enough information for a question, generate fewer questions "
    "rather than inventing content. Output only valid JSON arrays. "
    "IMPORTANT: Never reference the source material in any question or explanation. "
    "Do NOT use phrases like 'According to the text', 'The book states', 'As described in the material', "
    "'In this chapter', 'The author says', 'Based on the provided material', or any similar phrasing. "
    "Write every question as a standalone, general knowledge question about the subject matter itself, "
    "as if the question has always existed independently of any specific document."
)

_SCHEMA_EXAMPLE = """[
  {
    "question": "What is X?",
    "answers": ["Option A", "Option B", "Option C", "Option D"],
    "correct_indices": [0],
    "multiple_select": false,
    "explanation": "Option A is correct because..."
  }
]"""


def _build_prompt(n: int, instructions: str, text: str) -> str:
    return (
        f"Generate exactly {n} multiple choice quiz questions from the material below.\n\n"
        f"Return ONLY a valid JSON array — no extra text, no markdown fences. "
        f"Each element must follow this schema:\n{_SCHEMA_EXAMPLE}\n\n"
        f"Rules:\n"
        f"- Exactly 4 answer options per question.\n"
        f"- Each answer option must be SHORT — fewer than 10 words. Prefer single terms, names, or brief phrases. "
        f"Rephrase or shorten content as needed to keep options concise, while keeping them unambiguous.\n"
        f"- correct_indices: 0-based indices of correct answers.\n"
        f"- Set multiple_select: true only when more than one answer is correct.\n"
        f"- Vary difficulty across questions.\n"
        f"- Every question and answer must come exclusively from the provided material below.\n\n"
        f"Supplemental instructions: {instructions}\n\n"
        f"Material:\n{text}"
    )


def _shuffle_answer_positions(q: dict) -> None:
    """Randomly permute answer order in-place; update correct_indices to match.
    LLMs strongly bias toward putting the correct answer at index 0 — this
    redistributes positions uniformly without touching any text."""
    answers = q["answers"]
    correct = q["correct_indices"]
    n = len(answers)
    perm = list(range(n))
    random.shuffle(perm)
    inverse = [0] * n
    for new_idx, old_idx in enumerate(perm):
        inverse[old_idx] = new_idx
    q["answers"] = [answers[old_idx] for old_idx in perm]
    q["correct_indices"] = sorted(inverse[c] for c in correct if 0 <= c < n)


def _parse_questions(
    raw: str,
    allowed_answer_counts: frozenset[int] = frozenset({4}),
    shuffle: bool = True,
) -> list:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        questions = json.loads(raw)
        validated = []
        for q in questions:
            if (
                isinstance(q.get("question"), str)
                and isinstance(q.get("answers"), list)
                and len(q["answers"]) in allowed_answer_counts
                and isinstance(q.get("correct_indices"), list)
                and q["correct_indices"]
            ):
                q.setdefault("multiple_select", len(q["correct_indices"]) > 1)
                q.setdefault("explanation", "")
                if shuffle:
                    _shuffle_answer_positions(q)
                validated.append(q)
        return validated
    except (json.JSONDecodeError, TypeError):
        return []


# ARC caps buffered (non-streaming) output at 8000 tokens; streaming lifts it.
ARC_MAX_TOKENS = 32_000


def _call_arc(system: str, user: str, temperature: float) -> str:
    client = OpenAI(
        api_key=os.getenv("OPEN_AI_API_KEY"),
        base_url=os.getenv("OPEN_AI_ENDPOINT"),
    )
    model = os.getenv("OPEN_AI_MODEL", "vt-arc-llm")
    # vt-arc-llm is a reasoning model and reasoning tokens can exhaust the
    # 8000-token buffered cap, leaving content empty — so always stream.
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=ARC_MAX_TOKENS,
        stream=True,
    )
    parts: list[str] = []
    finish_reason: str | None = None
    for event in stream:
        if not event.choices:
            continue
        choice = event.choices[0]
        if choice.delta.content:
            parts.append(choice.delta.content)
        if choice.finish_reason:
            finish_reason = choice.finish_reason
    content = "".join(parts).strip()
    if not content:
        raise RuntimeError(
            f"{model} returned no content (finish_reason={finish_reason!r}). "
            f"If this is 'length', the input chunk is too large for the {ARC_MAX_TOKENS}-token output cap."
        )
    return content


def _call_claude(system: str, user: str) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    response = client.messages.create(
        model=model,
        max_tokens=8096,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # Models with extended thinking enabled prepend a thinking block, so the
    # text is not guaranteed to be at index 0; scan for the first text block.
    text_block = next(
        (block.text for block in response.content if block.type == "text"),
        None,
    )
    if text_block is None:
        block_types = [block.type for block in response.content]
        raise TypeError(f"Expected a text block in response but got {block_types}")
    return text_block.strip()


def _call_llm(provider: str, system: str, user: str, temperature: float) -> str:
    # temperature applies to ARC only; Claude calls keep the model default.
    if provider == PROVIDER_CLAUDE:
        return _call_claude(system, user)
    return _call_arc(system, user, temperature)


QUESTIONS_PER_CHUNK = 25
MIN_QUESTIONS_PER_CHUNK = 5


def _questions_for_chunk(chunk: str) -> int:
    from utils.pdf_utils import MAX_CHARS_PER_CHUNK
    scaled = round(len(chunk) / MAX_CHARS_PER_CHUNK * QUESTIONS_PER_CHUNK)
    return max(MIN_QUESTIONS_PER_CHUNK, scaled)


def generate_questions(
    text_chunks: list[str],
    supplemental_instructions: str = DEFAULT_INSTRUCTIONS,
    provider: str = PROVIDER_ARC,
) -> list:
    all_questions = []
    for chunk in text_chunks:
        n = _questions_for_chunk(chunk)
        raw = _call_llm(provider, _SYSTEM_PROMPT, _build_prompt(n, supplemental_instructions, chunk), 0.7)
        all_questions.extend(_parse_questions(raw))

    return all_questions


# ── Import pre-built questions (extraction only, no generation) ──────────────

# Smaller than MAX_CHARS_PER_CHUNK: extraction output is roughly as long as the
# input, so a large chunk would overflow the model's output token budget.
IMPORT_CHARS_PER_CHUNK = 12_000
IMPORT_ANSWER_COUNTS = frozenset({2, 3, 4})  # game UI has 4 answer slots

_IMPORT_SYSTEM_PROMPT = (
    "You convert an existing, human-written quiz into structured JSON. "
    "You are a transcriber, not an author: never write new questions, never add, remove, "
    "or reword answer options, and never change which answer is marked correct. "
    "Preserve the question and answer wording exactly, only stripping list markers such as "
    "'1.', 'Q3:', 'a)', '(B)', or '*' and any answer-key annotations. "
    "Output only valid JSON arrays."
)


def _build_import_prompt(text: str) -> str:
    return (
        "Extract every multiple choice question from the quiz text below.\n\n"
        "Return ONLY a valid JSON array — no extra text, no markdown fences. "
        f"Each element must follow this schema:\n{_SCHEMA_EXAMPLE}\n\n"
        "Rules:\n"
        "- Keep answer options in the order they appear.\n"
        "- The correct answer(s) may be called out in many ways: an 'Answer:' or 'Correct:' line, "
        "an answer key at the end, bold/asterisk/checkmark markers, '(correct)' tags, etc. "
        "Use whatever the text indicates.\n"
        "- correct_indices: 0-based indices of the correct answers.\n"
        "- Set multiple_select: true only when more than one answer is correct.\n"
        "- True/False questions become two options: [\"True\", \"False\"].\n"
        "- explanation: copy any explanation/rationale given for the question, otherwise \"\".\n"
        "- Skip any question that has no identifiable correct answer or more than 4 options.\n\n"
        f"Quiz text:\n{text}"
    )


def import_questions(text_chunks: list[str], provider: str = PROVIDER_ARC) -> list:
    """Parse pre-written questions (with answers called out) into bank format."""
    all_questions = []
    for chunk in text_chunks:
        raw = _call_llm(provider, _IMPORT_SYSTEM_PROMPT, _build_import_prompt(chunk), 0.0)
        all_questions.extend(
            _parse_questions(raw, allowed_answer_counts=IMPORT_ANSWER_COUNTS, shuffle=False)
        )
    return all_questions
