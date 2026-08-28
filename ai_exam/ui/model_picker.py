"""Two-tier model picker for the Streamlit Run page.

Each tier (HIGH / LOW) gets a (provider, model) pair. The provider drives the
model list:

- **anthropic** — curated, sourced from `ANTHROPIC_MODEL_OPUS/_SONNET/_HAIKU`
  in `.env`.
- **arc** — pulled live from the ARC proxy's OpenAI-compatible `/models`
  endpoint (`ARC_ENDPOINT`, bearer `ARC_API_KEY`). ARC's roster moves — model
  aliases are added and re-pointed without notice — so it is never hardcoded.
- **ollama** — pulled live from `localhost:11434/api/tags`, minus embedding
  models.

Both live lists fall back to the single configured id from `.env`
(`ARC_MODEL` / `OLLAMA_MODEL`) when the service is unreachable, so the picker
still renders something usable offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import httpx
import streamlit as st

import config as _cfg


_PROVIDERS = ["ollama", "arc", "anthropic"]


def _anthropic_models() -> list[str]:
    """Anthropic model ids offered in the picker, sourced from `.env`/config
    (ANTHROPIC_MODEL_OPUS / _SONNET / _HAIKU) so editing `.env` changes the
    dropdown. De-duplicated, order preserved (opus, sonnet, haiku)."""
    ordered = [
        _cfg.ANTHROPIC_MODEL_OPUS,
        _cfg.ANTHROPIC_MODEL_SONNET,
        _cfg.ANTHROPIC_MODEL_HAIKU,
    ]
    seen: set[str] = set()
    return [m for m in ordered if m and not (m in seen or seen.add(m))]


def _arc_models(endpoint: str) -> list[str]:
    """List every model the ARC proxy currently serves.

    ARC is OpenAI-compatible, so `GET {endpoint}/models` returns
    `{"data": [{"id": ...}, ...]}`. Every id there is routable, including the
    `preset` entries (thinking variants, `thinkinglatest`, `vision`), so
    nothing is filtered out — the proxy is the authority on what exists.

    Cached for 5 minutes via `st.cache_data`; the roster changes rarely and
    this is a network round-trip on every Run-page rerun otherwise. Returns
    `[]` when ARC is unreachable or no key is configured — the caller
    substitutes the `.env` default.
    """

    @st.cache_data(ttl=300.0, show_spinner=False)
    def _query(endpoint_: str) -> list[str]:
        # Read the key inside so it never becomes part of the cache key.
        if not _cfg.ARC_API_KEY:
            return []
        try:
            r = httpx.get(
                f"{endpoint_.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {_cfg.ARC_API_KEY}"},
                timeout=8.0,
            )
            r.raise_for_status()
            payload = r.json()
        except (httpx.HTTPError, ValueError):
            # ARC is an optional provider; an outage or a missing/expired key
            # must not take the Run page down. Caller falls back to ARC_MODEL.
            return []
        ids: list[str] = [m.get("id", "") for m in payload.get("data", [])]
        return sorted({i for i in ids if i}, key=str.lower)

    return _query(endpoint)


@dataclass(frozen=True)
class TierChoice:
    provider: str
    model: str


def _ollama_models(host: str = "http://localhost:11434") -> list[str]:
    """List chat-capable models on the local Ollama server.

    Filters out embedding models so the picker only shows things that work
    as a chat completion endpoint. Cached for the streamlit session via
    `st.cache_data` so we don't hit the API on every rerun.
    """

    @st.cache_data(ttl=60.0, show_spinner=False)
    def _query(host_: str) -> list[str]:
        try:
            r = httpx.get(f"{host_}/api/tags", timeout=2.0)
            r.raise_for_status()
            names: list[str] = [m.get("name", "") for m in r.json().get("models", [])]
            return sorted(n for n in names if n and "embed" not in n)
        except (httpx.HTTPError, ValueError):
            return []

    return _query(host)


def _models_for(provider: str, fallback: str) -> list[str]:
    if provider == "anthropic":
        return _anthropic_models()
    if provider == "arc":
        return _arc_models(_cfg.ARC_ENDPOINT) or [_cfg.ARC_MODEL]
    if provider == "ollama":
        models = _ollama_models()
        if not models:
            return [fallback]
        return models
    return [fallback]


def render_tier_picker(
    *,
    label: str,
    help: str,
    state_key_provider: str,
    state_key_model: str,
    ollama_fallback_model: str,
    default: TierChoice,
) -> TierChoice:
    """Render one tier's (provider, model) selector pair side-by-side."""
    st.markdown(f"**{label}**")
    st.caption(help)
    cols = st.columns([1, 2])
    with cols[0]:
        cur_provider = st.session_state.get(state_key_provider, default.provider)
        if cur_provider not in _PROVIDERS:
            cur_provider = default.provider
        provider = st.selectbox(
            "Provider",
            _PROVIDERS,
            index=_PROVIDERS.index(cur_provider),
            key=state_key_provider,
            label_visibility="collapsed",
        )
    models = _models_for(provider, ollama_fallback_model)
    if provider == "arc" and not _arc_models(_cfg.ARC_ENDPOINT):
        st.caption(
            f":orange[Could not reach ARC at {_cfg.ARC_ENDPOINT} "
            f"(or ARC_API_KEY is unset) — showing the `.env` default only.]"
        )
    with cols[1]:
        cur_model = st.session_state.get(state_key_model, default.model)
        # If the saved model isn't valid for the newly-chosen provider, snap
        # to the first available model for this provider.
        if cur_model not in models:
            cur_model = models[0]
            # Streamlit will overwrite session state when the widget renders;
            # set it now so the selectbox shows the right initial value.
            st.session_state[state_key_model] = cur_model
        model = st.selectbox(
            "Model",
            models,
            index=models.index(cur_model),
            key=state_key_model,
            label_visibility="collapsed",
        )
    return TierChoice(provider=provider, model=model)
