"""AI provider registry — which chat provider the agent talks to.

All supported providers speak the OpenAI-compatible chat API. The active
provider and model are chosen from the environment (.env):

  AI_PROVIDER=fireworks|openai|featherless|custom   (optional; default:
               first configured provider, fireworks first)
  fireworks   — FIREWORKS_API_KEY + FIREWORKS_MODEL (+ FIREWORKS_VISION_MODEL)
  openai      — OPENAI_API_KEY (+ optional OPENAI_MODEL, default gpt-4o-mini)
  featherless — FEATHERLESS_API_KEY + FEATHERLESS_MODEL
  custom      — CUSTOM_API_KEY + CUSTOM_BASE_URL + CUSTOM_MODEL
                (any other OpenAI-compatible provider: Together, Groq,
                OpenRouter, local vLLM, ...)

API keys are read from the environment at call time and never leave this
module. Changes take effect on server restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from prosurface_agent import config

# Order used when AI_PROVIDER is not set: first configured provider wins.
DEFAULT_ORDER = ("fireworks", "openai", "featherless", "custom")

# Env var that sets the model per provider (shown in error messages).
MODEL_ENV = {
    "fireworks": "FIREWORKS_MODEL",
    "openai": "OPENAI_MODEL",
    "featherless": "FEATHERLESS_MODEL",
    "custom": "CUSTOM_MODEL",
}


class ProviderError(Exception):
    """Unknown or unconfigured provider. Message is safe to show."""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    base_url: str
    api_key_env: str
    curated_models: Tuple[str, ...] = ()
    vision_model: Optional[str] = None  # None -> use the selected chat model


def spec(pid: str) -> ProviderSpec:
    """Return the (freshly built) spec for a known provider id."""
    if pid == "fireworks":
        curated = []
        if config.FIREWORKS_MODEL:
            curated.append(config.FIREWORKS_MODEL)
        if config.FIREWORKS_VISION_MODEL and config.FIREWORKS_VISION_MODEL not in curated:
            curated.append(config.FIREWORKS_VISION_MODEL)
        return ProviderSpec(
            id="fireworks",
            name="Fireworks AI",
            base_url=config.FIREWORKS_BASE_URL,
            api_key_env="FIREWORKS_API_KEY",
            curated_models=tuple(curated),
            vision_model=config.FIREWORKS_VISION_MODEL,
        )
    if pid == "featherless":
        return ProviderSpec(
            id="featherless",
            name="Featherless AI",
            base_url="https://api.featherless.ai/v1",
            api_key_env="FEATHERLESS_API_KEY",
        )
    if pid == "openai":
        return ProviderSpec(
            id="openai",
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            curated_models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o3"),
        )
    if pid == "custom":
        custom_model = os.getenv("CUSTOM_MODEL", "").strip()
        return ProviderSpec(
            id="custom",
            name="Custom (OpenAI-compatible)",
            base_url=os.getenv("CUSTOM_BASE_URL", "").strip(),
            api_key_env="CUSTOM_API_KEY",
            curated_models=(custom_model,) if custom_model else (),
        )
    raise ProviderError(f"Unknown provider '{pid}'.")


def is_configured(pid: str) -> bool:
    """True if this provider can be used right now (key present)."""
    try:
        s = spec(pid)
    except ProviderError:
        return False
    if not os.getenv(s.api_key_env, "").strip():
        return False
    if pid == "custom" and not s.base_url:
        return False
    return True


def resolve(pid: str) -> Tuple[str, str]:
    """Return (api_key, base_url) for a configured provider.

    Raises ProviderError (with a plain-English message) for unknown or
    unconfigured providers. Callers must never log or return the api_key.
    """
    s = spec(pid)  # raises ProviderError for unknown ids
    if pid == "custom" and not s.base_url:
        raise ProviderError("Custom provider needs CUSTOM_BASE_URL in the .env file.")
    api_key = os.getenv(s.api_key_env, "").strip()
    if not api_key:
        raise ProviderError(
            f"{s.name} has no API key yet — add {s.api_key_env}=... to the "
            f".env file and restart the server."
        )
    return api_key, s.base_url


def default_model(pid: str) -> str:
    """Model for this provider from its .env var, with a sane fallback."""
    if pid == "fireworks":
        return config.FIREWORKS_MODEL
    if pid == "openai":
        return os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    if pid == "featherless":
        return os.getenv("FEATHERLESS_MODEL", "").strip()
    if pid == "custom":
        return os.getenv("CUSTOM_MODEL", "").strip()
    return ""


def default_selection() -> Tuple[str, str]:
    """Startup default: AI_PROVIDER if set, else first usable provider."""
    forced = os.getenv("AI_PROVIDER", "").strip().lower()
    if forced:
        try:
            s = spec(forced)
        except ProviderError:
            raise ProviderError(
                f"AI_PROVIDER={forced} is not a known provider — use "
                f"fireworks, openai, featherless or custom."
            )
        if not is_configured(forced):
            raise ProviderError(
                f"AI_PROVIDER={forced} but {s.api_key_env} is not set in .env."
            )
        model = default_model(forced)
        if not model:
            raise ProviderError(
                f"AI_PROVIDER={forced} needs a model — set "
                f"{MODEL_ENV[forced]} in .env."
            )
        return forced, model
    for pid in DEFAULT_ORDER:
        if is_configured(pid):
            model = default_model(pid)
            if model:
                return pid, model
    raise ProviderError(
        "No AI provider is configured. Add FIREWORKS_API_KEY and "
        "FIREWORKS_MODEL (or OPENAI_API_KEY / FEATHERLESS_API_KEY + "
        "FEATHERLESS_MODEL / CUSTOM_BASE_URL + CUSTOM_API_KEY + CUSTOM_MODEL) "
        "to the .env file and restart."
    )
