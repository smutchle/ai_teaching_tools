"""Configuration: env loading, LLM provider clients, per-agent model registry.

Two providers are wired:

- **Anthropic** (claude-opus-4-7 / claude-sonnet-4-6) — original path.
- **ARC** — OpenAI-compatible endpoint at the VT ARC LLM proxy, serving
  gpt-oss-120b today; routable to other ARC-hosted models by changing
  `ARC_MODEL`.

`MODEL_REGISTRY` maps each agent persona to a `(provider_kind, model_id)`
choice. `make_provider(persona_name)` returns the right provider instance —
agents never construct their own client.

The default routes every agent to ARC's `gpt-oss-120b` (free; substantial
reasoning capability). To send a specific agent back to Anthropic, edit its
entry in `MODEL_REGISTRY` to `_ANTHROPIC_OPUS` or `_ANTHROPIC_SONNET`.
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from openai import OpenAI

from providers import AnthropicProvider, LLMProvider, OpenAIProvider


# Load .env from the project root the first time this module is imported.
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"required env var {name} is not set (check .env)")
    return val or ""


# Anthropic — only required if any persona is routed to the "anthropic" provider.
ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL_OPUS: str = _env("ANTHROPIC_MODEL_OPUS", "claude-opus-4-7")
ANTHROPIC_MODEL_SONNET: str = _env("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")

# ARC (OpenAI-compatible endpoint) — required if any persona is routed to "arc".
ARC_ENDPOINT: str = _env("ARC_ENDPOINT", "https://llm-api.arc.vt.edu/api/v1")
ARC_API_KEY: str = _env("ARC_API_KEY")
ARC_MODEL: str = _env("ARC_MODEL", "gpt-oss-120b")

# Ollama — both embeddings (always) and chat (if any persona is routed to "ollama").
OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_EMBED_MODEL: str = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text-v2-moe:latest")
OLLAMA_MODEL: str = _env("OLLAMA_MODEL", "gemma4:31b")
# Ollama exposes an OpenAI-compatible chat endpoint under /v1.
OLLAMA_ENDPOINT: str = _env("OLLAMA_ENDPOINT", f"{OLLAMA_HOST.rstrip('/')}/v1")


@dataclass(frozen=True)
class ModelChoice:
    """One persona's provider + model. Values in MODEL_REGISTRY."""

    provider: str  # "anthropic" or "arc"
    model: str


_ARC_DEFAULT = ModelChoice(provider="arc", model=ARC_MODEL)
_OLLAMA_DEFAULT = ModelChoice(provider="ollama", model=OLLAMA_MODEL)
_ANTHROPIC_OPUS = ModelChoice(provider="anthropic", model=ANTHROPIC_MODEL_OPUS)
_ANTHROPIC_SONNET = ModelChoice(provider="anthropic", model=ANTHROPIC_MODEL_SONNET)


# Tier mapping per the design doc §8. The HIGH tier carries creative /
# heavy-reasoning agents whose output quality dominates the final exam;
# the LOW tier carries verification + audit agents whose work is mostly
# pattern matching and rule application. A typical run is ~25-30 HIGH
# calls and ~80-100 LOW calls, so routing HIGH to a strong paid model and
# LOW to a free local model captures most of the cost win without
# sacrificing item quality.
PERSONA_TIER: dict[str, str] = {
    "sme":                         "high",
    "blueprint_architect":         "high",
    "adversarial_student":         "high",
    "item_writing_specialist":     "low",
    "learning_outcomes_alignment": "low",
    "grounding_verifier":          "low",
    "accessibility":               "low",
    "psychometrician":             "low",
    "narrator":                    "low",  # post-run narrative polish
    "spec_suggester":              "low",  # UI-only: draft CourseSpec from uploaded materials
}


# Per-persona routing. Default: everything → local Ollama (free, no rate
# limit). Override via override_provider/override_tiers before any agent is
# constructed, or by editing entries directly.
MODEL_REGISTRY: dict[str, ModelChoice] = {
    p: _OLLAMA_DEFAULT for p in PERSONA_TIER
}
# Narrator is a one-shot post-run polish — default to Haiku for cheap+fast.
# Users can flip everything via override_tiers / override_provider, in which
# case the narrator follows.
MODEL_REGISTRY["narrator"] = ModelChoice(
    provider="anthropic", model="claude-haiku-4-5-20251001",
)
# Spec Suggester is also a one-shot UI utility — cheap, fast, Haiku.
MODEL_REGISTRY["spec_suggester"] = ModelChoice(
    provider="anthropic", model="claude-haiku-4-5-20251001",
)


# Default per-provider model id when the caller asks for a provider but
# leaves the model unspecified.
_DEFAULT_MODEL_FOR_PROVIDER: dict[str, str] = {
    "ollama":    OLLAMA_MODEL,
    "arc":       ARC_MODEL,
    "anthropic": ANTHROPIC_MODEL_SONNET,
}


def make_choice(provider: str, model: str | None = None) -> ModelChoice:
    """Build a ModelChoice from a provider name plus an optional explicit
    model id. If `model` is None, use the provider's default."""
    if provider not in _DEFAULT_MODEL_FOR_PROVIDER:
        raise ValueError(
            f"unknown provider {provider!r}; "
            f"choose one of {sorted(_DEFAULT_MODEL_FOR_PROVIDER)}"
        )
    return ModelChoice(
        provider=provider,
        model=model or _DEFAULT_MODEL_FOR_PROVIDER[provider],
    )


def override_tiers(high: ModelChoice, low: ModelChoice) -> None:
    """Route every persona to `high` or `low` based on PERSONA_TIER.

    Used by the run.py --high-*/--low-* CLI flags and by the Streamlit
    Run page's two-tier selector to flip the roster without editing
    MODEL_REGISTRY entries one at a time.
    """
    for persona, tier in PERSONA_TIER.items():
        MODEL_REGISTRY[persona] = high if tier == "high" else low


def override_provider(provider: str) -> None:
    """Shortcut: flip every persona to a single provider using that
    provider's default model. Equivalent to `override_tiers(c, c)` where
    `c = make_choice(provider)`."""
    choice = make_choice(provider)
    override_tiers(choice, choice)


# Lazy singleton clients so we only construct what we need. If only one
# provider is in the registry, the other clients are never touched.
_anthropic_client: Anthropic | None = None
_arc_client: OpenAI | None = None
_ollama_client: OpenAI | None = None

# Module-level concurrency gates — shared across all agents pointing at the
# same backend. The Moderator fans out aggressively (3 critics × N items, 8
# Phase-2 cells in parallel, etc.); without a cap, single-GPU Ollama
# serializes requests internally and the SDK times out. ARC's free quota
# is 30 req/hr, so cap=4 stays gentle there too.
_anthropic_sem = threading.Semaphore(8)
_arc_sem = threading.Semaphore(4)
_ollama_sem = threading.Semaphore(1)

# Per-provider request timeout (seconds). Local Ollama on a 31B model can
# take several minutes per call when the prompt is large; the SDK default
# (~10 min) is sometimes too tight when calls also queue behind the cap.
_OLLAMA_TIMEOUT_S = 1800.0   # 30 min — generous to absorb queue wait
_ARC_TIMEOUT_S = 600.0       # 10 min
_ANTHROPIC_TIMEOUT_S = 600.0 # 10 min

# Thinking models (gemma4, gpt-oss, etc.) emit thousands of tokens to a
# hidden `reasoning` channel before producing JSON `content`. The default
# 4096 max_tokens caps the whole completion (reasoning + content); when
# reasoning eats it all, content comes back empty and the call fails. A
# 16k floor gives the model room for both. Cloud Anthropic models don't
# need this — they don't surface a separate reasoning stream.
_OLLAMA_MAX_TOKENS_FLOOR = 16384


def _get_anthropic_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for any persona routed to the "
                "'anthropic' provider. Set it in .env or change MODEL_REGISTRY."
            )
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _get_arc_client() -> OpenAI:
    global _arc_client
    if _arc_client is None:
        if not ARC_API_KEY:
            raise RuntimeError(
                "ARC_API_KEY is required for any persona routed to the 'arc' "
                "provider. Set it in .env or change MODEL_REGISTRY."
            )
        _arc_client = OpenAI(base_url=ARC_ENDPOINT, api_key=ARC_API_KEY)
    return _arc_client


def _get_ollama_client() -> OpenAI:
    """Ollama doesn't authenticate by default; pass a sentinel api_key."""
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = OpenAI(base_url=OLLAMA_ENDPOINT, api_key="ollama")
    return _ollama_client


def make_provider(persona_name: str) -> LLMProvider:
    """Return the configured provider for an agent persona."""
    if persona_name not in MODEL_REGISTRY:
        raise KeyError(
            f"no model assignment for persona {persona_name!r}; "
            f"known: {sorted(MODEL_REGISTRY)}"
        )
    choice = MODEL_REGISTRY[persona_name]
    if choice.provider == "anthropic":
        return AnthropicProvider(
            _get_anthropic_client(), model=choice.model,
            concurrency_sem=_anthropic_sem, timeout=_ANTHROPIC_TIMEOUT_S,
        )
    if choice.provider == "arc":
        return OpenAIProvider(
            _get_arc_client(), model=choice.model,
            concurrency_sem=_arc_sem, timeout=_ARC_TIMEOUT_S,
        )
    if choice.provider == "ollama":
        return OpenAIProvider(
            _get_ollama_client(), model=choice.model,
            concurrency_sem=_ollama_sem, timeout=_OLLAMA_TIMEOUT_S,
            max_tokens_floor=_OLLAMA_MAX_TOKENS_FLOOR,
        )
    raise ValueError(
        f"unknown provider {choice.provider!r} for persona {persona_name!r}"
    )


def model_for(persona_name: str) -> str:
    """Convenience: the model id assigned to a persona."""
    return MODEL_REGISTRY[persona_name].model


def make_anthropic_client() -> Anthropic:
    """Back-compat shim — preserved so the few callers that still want the raw
    Anthropic SDK (e.g., direct probes from notebooks) can get it. Agents
    should use `make_provider(persona)` instead."""
    return _get_anthropic_client()
