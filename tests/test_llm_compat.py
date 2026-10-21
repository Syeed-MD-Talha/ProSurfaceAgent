"""ChatCompat adaptive parameter handling. Fake client, no network calls.

Error texts mirror the real API rejections observed from gpt-6-luna:
max_tokens unsupported, tools needing reasoning_effort='none', and
custom temperature rejected.
"""

import unittest
from types import SimpleNamespace

from prosurface_agent.context.compressor import ContextCompressor
from prosurface_agent.llm import ChatCompat

ERR_MAX_TOKENS = (
    "Error code: 400 - Unsupported parameter: 'max_tokens' is not supported "
    "with this model. Use 'max_completion_tokens' instead."
)
ERR_REASONING = (
    "Function tools with reasoning_effort are not supported for this model "
    "in /v1/chat/completions. To use function tools, use /v1/responses or "
    "set reasoning_effort to 'none'."
)
ERR_TEMPERATURE = (
    "Unsupported value: 'temperature' does not support 0.3 with this model. "
    "Only the default (1) value is supported."
)


class _FakeCompletions:
    """Replays a behavior script: ('raise', msg) or ('ok', content)."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        idx = min(len(self.calls), len(self.behavior)) - 1
        kind, val = self.behavior[idx]
        if kind == "raise":
            raise Exception(val)
        msg = SimpleNamespace(content=val, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, behavior):
        self.chat = SimpleNamespace(completions=_FakeCompletions(behavior))

    @property
    def calls(self):
        return self.chat.completions.calls


def _create(compat, client, **overrides):
    kwargs = dict(model="m", messages=[{"role": "user", "content": "hi"}], token_limit=100)
    kwargs.update(overrides)
    return compat.create(client, **kwargs)


class TestChatCompat(unittest.TestCase):
    def test_classic_model_passes_max_tokens_through(self):
        client = _FakeClient([("ok", "hello")])
        resp = _create(ChatCompat(), client)
        self.assertEqual(resp.choices[0].message.content, "hello")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_tokens"], 100)

    def test_adapts_token_param_and_remembers(self):
        client = _FakeClient([("raise", ERR_MAX_TOKENS), ("ok", "hi")])
        compat = ChatCompat()
        _create(compat, client)
        self.assertEqual(compat.token_param, "max_completion_tokens")
        self.assertEqual(client.calls[1]["max_completion_tokens"], 100)
        self.assertNotIn("max_tokens", client.calls[1])
        # remembered: next call goes straight to the new param, no extra error
        _create(compat, client)
        self.assertEqual(len(client.calls), 3)
        self.assertIn("max_completion_tokens", client.calls[2])

    def test_adapts_reasoning_effort_for_tools(self):
        client = _FakeClient([("raise", ERR_REASONING), ("ok", "hi")])
        compat = ChatCompat()
        tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        _create(compat, client, tools=tools, tool_choice="auto")
        self.assertEqual(compat.reasoning_effort, "none")
        self.assertEqual(client.calls[1]["reasoning_effort"], "none")
        self.assertEqual(client.calls[1]["tools"], tools)

    def test_drops_temperature_when_rejected(self):
        client = _FakeClient([("raise", ERR_TEMPERATURE), ("ok", "hi")])
        compat = ChatCompat()
        _create(compat, client, temperature=0.3)
        self.assertFalse(compat.temperature_ok)
        self.assertNotIn("temperature", client.calls[1])

    def test_temperature_kept_when_accepted(self):
        client = _FakeClient([("ok", "hi")])
        compat = ChatCompat()
        _create(compat, client, temperature=0.3)
        self.assertEqual(client.calls[0]["temperature"], 0.3)

    def test_converges_through_multiple_rejections(self):
        client = _FakeClient(
            [
                ("raise", ERR_MAX_TOKENS),
                ("raise", ERR_REASONING),
                ("raise", ERR_TEMPERATURE),
                ("ok", "finally"),
            ]
        )
        compat = ChatCompat()
        resp = _create(compat, client, tools=[{"x": 1}], temperature=0.3)
        self.assertEqual(resp.choices[0].message.content, "finally")
        final = client.calls[-1]
        self.assertIn("max_completion_tokens", final)
        self.assertEqual(final["reasoning_effort"], "none")
        self.assertNotIn("temperature", final)

    def test_unrelated_error_is_reraised(self):
        client = _FakeClient([("raise", "boom: connection refused")])
        with self.assertRaises(Exception) as ctx:
            _create(ChatCompat(), client)
        self.assertIn("boom", str(ctx.exception))

    def test_adaptation_limit_raises(self):
        client = _FakeClient([("raise", ERR_MAX_TOKENS)] * 10)
        # adaptation keeps succeeding but the fake keeps failing the new param
        compat = ChatCompat()
        # after the first failure the token param flips; the fake error keeps
        # mentioning both params so _adapt returns False on attempt 2
        with self.assertRaises(Exception):
            _create(compat, client)


class TestCompressorCompat(unittest.TestCase):
    def test_summary_uses_compat_and_adapts(self):
        client = _FakeClient([("raise", ERR_TEMPERATURE), ("ok", "the summary")])
        compat = ChatCompat()
        comp = ContextCompressor(client, "m", compat=compat)
        summary = comp._generate_summary([{"role": "user", "content": "x"}])
        self.assertEqual(summary, "the summary")
        self.assertFalse(compat.temperature_ok)
        self.assertNotIn("temperature", client.calls[-1])

    def test_summary_falls_back_on_unrelated_error(self):
        client = _FakeClient([("raise", "boom")])
        comp = ContextCompressor(client, "m", compat=ChatCompat())
        summary = comp._generate_summary([{"role": "user", "content": "hello"}])
        self.assertIn("Detailed Session Log", summary)  # deterministic fallback


if __name__ == "__main__":
    unittest.main()
