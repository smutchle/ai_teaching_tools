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


def _parse_questions(raw: str) -> list:
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
                and len(q["answers"]) == 4
                and isinstance(q.get("correct_indices"), list)
            ):
                q.setdefault("multiple_select", len(q["correct_indices"]) > 1)
                q.setdefault("explanation", "")
                _shuffle_answer_positions(q)
                validated.append(q)
        return validated
    except (json.JSONDecodeError, TypeError):
        return []


def _generate_arc(text: str, n: int, instructions: str) -> list:
    client = OpenAI(
        api_key=os.getenv("OPEN_AI_API_KEY"),
        base_url=os.getenv("OPEN_AI_ENDPOINT"),
    )
    model = os.getenv("OPEN_AI_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(n, instructions, text)},
        ],
        temperature=0.7,
    )
    return _parse_questions(response.choices[0].message.content.strip())


def _generate_claude(text: str, n: int, instructions: str) -> list:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    response = client.messages.create(
        model=model,
        max_tokens=8096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(n, instructions, text)}],
    )
    return _parse_questions(response.content[0].text.strip())


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
    _fn = _generate_claude if provider == PROVIDER_CLAUDE else _generate_arc

    all_questions = []
    for chunk in text_chunks:
        n = _questions_for_chunk(chunk)
        all_questions.extend(_fn(chunk, n, supplemental_instructions))

    return all_questions
