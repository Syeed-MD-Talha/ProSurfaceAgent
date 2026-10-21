"""Adaptive parameter handling for OpenAI-compatible chat models.

Newer models (e.g. OpenAI reasoning-style models like gpt-6-luna) reject
parameters that older models accept:

- ``max_tokens``              -> must be ``max_completion_tokens``
- custom ``temperature``      -> only the default is allowed
- function tools              -> only with ``reasoning_effort="none"``

ChatCompat remembers what the active model accepts and adapts on error,
so the rest of the codebase can call the API the classic way. State is
per-model: Agent creates a fresh ChatCompat whenever the model changes.
"""

from __future__ import annotations

MAX_ADAPT_ATTEMPTS = 4


class ChatCompat:
    """Per-model memory of which chat parameters are accepted."""

    def __init__(self) -> None:
        self.token_param = "max_tokens"  # flips to "max_completion_tokens"
        self.reasoning_effort = None  # becomes "none" if the model demands it
        self.temperature_ok = True  # flips False if custom temps are rejected

    def create(
        self,
        client,
        *,
        model: str,
        messages,
        token_limit: int,
        tools=None,
        tool_choice=None,
        temperature=None,
        stream: bool = False,
        timeout: float = 120,
    ):
        """client.chat.completions.create with adaptive parameters.

        Retries up to MAX_ADAPT_ATTEMPTS times, adapting one rejected
        parameter per attempt and remembering the adaptation for all
        future calls. Re-raises errors that are not parameter-related.
        """
        for _ in range(MAX_ADAPT_ATTEMPTS):
            kwargs = self._build(
                model, messages, token_limit, tools, tool_choice,
                temperature, stream, timeout,
            )
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as e:
                if not self._adapt(str(e)):
                    raise
        raise RuntimeError("model parameter adaptation did not converge")

    def _build(
        self, model, messages, token_limit, tools, tool_choice,
        temperature, stream, timeout,
    ) -> dict:
        kwargs = {
            "model": model,
            "messages": messages,
            "timeout": timeout,
            self.token_param: token_limit,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if temperature is not None and self.temperature_ok:
            kwargs["temperature"] = temperature
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if stream:
            kwargs["stream"] = True
        return kwargs

    def _adapt(self, message: str) -> bool:
        """Learn from an API error message. True if something changed."""
        changed = False
        if (
            self.token_param == "max_tokens"
            and "max_tokens" in message
            and "max_completion_tokens" in message
        ):
            self.token_param = "max_completion_tokens"
            changed = True
        if (
            self.reasoning_effort is None
            and "reasoning_effort" in message
            and "'none'" in message
        ):
            self.reasoning_effort = "none"
            changed = True
        if (
            self.temperature_ok
            and "temperature" in message
            and "default" in message
        ):
            self.temperature_ok = False
            changed = True
        return changed
