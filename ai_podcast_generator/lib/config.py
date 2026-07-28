"""Settings loaded from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_DIR: Path = Path(__file__).resolve().parent.parent
ENV_PATH: Path = APP_DIR / ".env"

# The zimage MCP server (Z-Image Turbo) on ads2. Reachable over plain HTTP from
# ads1, so the app speaks MCP JSON-RPC to it directly.
DEFAULT_ZIMAGE_URL = "http://ads2.datasci.vt.edu:8765/mcp"

# Scripts are written by Claude directly rather than through the ARC proxy: the
# proxy's models were the limiting factor on script quality, and its 8000-token
# buffered cap forced awkward workarounds. There is no endpoint to configure -
# the Anthropic SDK knows its own.
DEFAULT_MODEL = "claude-opus-5"


@dataclass(frozen=True)
class Settings:
    """Connection settings for the Anthropic API and zimage."""

    api_key: str
    model: str
    zimage_url: str
    eleven_labs_api_key: str


def _setting(name: str, file_values: dict[str, str | None]) -> str:
    """A setting from .env, falling back to the ambient environment.

    The file wins deliberately. ANTHROPIC_API_KEY is a common name that other
    tooling exports globally, and silently authenticating against whatever key
    happened to be in the shell - rather than the one written next to the app -
    is the kind of failure that looks like a billing mystery rather than a bug.
    """
    from_file = (file_values.get(name) or "").strip()
    return from_file or os.environ.get(name, "").strip()


def load_settings() -> Settings:
    """Read .env. Raises if the API key is absent, since nothing works without it."""
    file_values = dotenv_values(ENV_PATH)

    api_key = _setting("ANTHROPIC_API_KEY", file_values)
    model = _setting("ANTHROPIC_MODEL", file_values)
    eleven_labs_api_key = _setting("ELEVEN_LABS_API_KEY", file_values)

    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", api_key),
            ("ELEVEN_LABS_API_KEY", eleven_labs_api_key),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing required setting(s) {', '.join(missing)} in {ENV_PATH}"
        )

    return Settings(
        api_key=api_key,
        model=model or DEFAULT_MODEL,
        zimage_url=_setting("ZIMAGE_MCP_URL", file_values) or DEFAULT_ZIMAGE_URL,
        eleven_labs_api_key=eleven_labs_api_key,
    )
