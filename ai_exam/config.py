"""Configuration: env loading, Anthropic client, model registry, Ollama host.

The model registry is the single source of truth for which Claude model each
agent uses. Per the design doc §8: Opus for the reasoning-heavy SME and
Adversarial Student, Sonnet for everything else. Override via env vars without
touching code.
"""

import os
from pathlib import Path
from typing import Mapping

from anthropic import Anthropic
from dotenv import load_dotenv


# Load .env from the project root the first time this module is imported.
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"required env var {name} is not set (check .env)")
    return val or ""


ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY", required=True)
ANTHROPIC_MODEL_OPUS: str = _env("ANTHROPIC_MODEL_OPUS", "claude-opus-4-7")
ANTHROPIC_MODEL_SONNET: str = _env("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")

OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_EMBED_MODEL: str = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text-v2-moe:latest")


# Per-agent model assignment. Keyed by BaseAgent.PERSONA_NAME.
MODEL_REGISTRY: Mapping[str, str] = {
    "sme": ANTHROPIC_MODEL_OPUS,
    "adversarial_student": ANTHROPIC_MODEL_OPUS,
    "blueprint_architect": ANTHROPIC_MODEL_SONNET,
    "item_writing_specialist": ANTHROPIC_MODEL_SONNET,
    "learning_outcomes_alignment": ANTHROPIC_MODEL_SONNET,
    "accessibility": ANTHROPIC_MODEL_SONNET,
    "psychometrician": ANTHROPIC_MODEL_SONNET,
    "grounding_verifier": ANTHROPIC_MODEL_SONNET,
}


def make_anthropic_client() -> Anthropic:
    return Anthropic(api_key=ANTHROPIC_API_KEY)


def model_for(persona_name: str) -> str:
    if persona_name not in MODEL_REGISTRY:
        raise KeyError(
            f"no model assignment for persona {persona_name!r}; "
            f"known: {sorted(MODEL_REGISTRY)}"
        )
    return MODEL_REGISTRY[persona_name]
