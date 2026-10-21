"""Provider/model selection from .env: registry, defaults, and switching.

No network calls. Env vars are patched with mock.patch.dict so the tests
do not depend on what is in .env. Agent.set_model is exercised with a
stubbed Agent (same pattern as test_service.py).
"""

import unittest
from unittest import mock

from prosurface_agent import providers
from prosurface_agent.agent import Agent
from prosurface_agent.llm import ChatCompat


class _StubCompressor:
    def __init__(self):
        self.client = None
        self.model = ""
        self.compat = None


class StubAgent(Agent):
    """Bypass __init__ (needs real credentials/stores) — set_model only."""

    def __init__(self):
        self.provider_id = "fireworks"
        self.client = object()
        self.model = "old-chat-model"
        self.vision_model = "old-vision-model"
        self.compat = ChatCompat()
        self.compressor = _StubCompressor()


_ALL_KEYS_EMPTY = {
    "FIREWORKS_API_KEY": "",
    "OPENAI_API_KEY": "",
    "FEATHERLESS_API_KEY": "",
    "CUSTOM_API_KEY": "",
    "CUSTOM_BASE_URL": "",
    "AI_PROVIDER": "",
}


class TestRegistry(unittest.TestCase):
    def test_known_provider_ids(self):
        for pid in ("fireworks", "featherless", "openai", "custom"):
            s = providers.spec(pid)
            self.assertEqual(s.id, pid)
            self.assertTrue(s.name)
            self.assertTrue(s.api_key_env)

    def test_builtin_base_urls(self):
        self.assertIn("fireworks.ai", providers.spec("fireworks").base_url)
        self.assertIn("featherless.ai", providers.spec("featherless").base_url)
        self.assertIn("openai.com", providers.spec("openai").base_url)

    def test_unknown_provider_raises(self):
        with self.assertRaises(providers.ProviderError):
            providers.spec("nope")

    def test_is_configured_requires_key(self):
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": ""}):
            self.assertFalse(providers.is_configured("featherless"))
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": "k"}):
            self.assertTrue(providers.is_configured("featherless"))

    def test_custom_requires_base_url(self):
        with mock.patch.dict(
            "os.environ", {"CUSTOM_API_KEY": "k", "CUSTOM_BASE_URL": ""}
        ):
            self.assertFalse(providers.is_configured("custom"))
        with mock.patch.dict(
            "os.environ", {"CUSTOM_API_KEY": "k", "CUSTOM_BASE_URL": "https://x/v1"}
        ):
            self.assertTrue(providers.is_configured("custom"))

    def test_resolve_unconfigured_raises_with_env_hint(self):
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": ""}):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.resolve("featherless")
            self.assertIn("FEATHERLESS_API_KEY", str(ctx.exception))

    def test_resolve_configured_returns_key_and_base(self):
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": "k"}):
            key, base = providers.resolve("featherless")
        self.assertEqual(key, "k")
        self.assertIn("featherless.ai", base)


class TestDefaultModel(unittest.TestCase):
    def test_openai_model_env_overrides_fallback(self):
        with mock.patch.dict("os.environ", {"OPENAI_MODEL": "my-model"}):
            self.assertEqual(providers.default_model("openai"), "my-model")
        with mock.patch.dict("os.environ", {"OPENAI_MODEL": ""}):
            self.assertEqual(providers.default_model("openai"), "gpt-4o-mini")

    def test_featherless_model_from_env(self):
        with mock.patch.dict("os.environ", {"FEATHERLESS_MODEL": "fl-model"}):
            self.assertEqual(providers.default_model("featherless"), "fl-model")
        with mock.patch.dict("os.environ", {"FEATHERLESS_MODEL": ""}):
            self.assertEqual(providers.default_model("featherless"), "")

    def test_custom_model_from_env(self):
        with mock.patch.dict("os.environ", {"CUSTOM_MODEL": "c-model"}):
            self.assertEqual(providers.default_model("custom"), "c-model")

    def test_fireworks_curated_includes_env_model(self):
        with mock.patch.object(providers.config, "FIREWORKS_MODEL", "fw-model"):
            s = providers.spec("fireworks")
        self.assertIn("fw-model", s.curated_models)
        self.assertEqual(s.vision_model, providers.config.FIREWORKS_VISION_MODEL)


class TestDefaultSelection(unittest.TestCase):
    def test_prefers_fireworks_when_ai_provider_unset(self):
        with mock.patch.dict(
            "os.environ", {"FIREWORKS_API_KEY": "k", "AI_PROVIDER": ""}
        ):
            with mock.patch.object(providers.config, "FIREWORKS_MODEL", "fw-model"):
                pid, model = providers.default_selection()
        self.assertEqual(pid, "fireworks")
        self.assertEqual(model, "fw-model")

    def test_falls_through_to_openai(self):
        env = {"FIREWORKS_API_KEY": "", "OPENAI_API_KEY": "k", "AI_PROVIDER": ""}
        with mock.patch.dict("os.environ", env):
            pid, model = providers.default_selection()
        self.assertEqual(pid, "openai")
        self.assertEqual(model, "gpt-4o-mini")

    def test_raises_when_nothing_configured(self):
        with mock.patch.dict("os.environ", _ALL_KEYS_EMPTY):
            with self.assertRaises(providers.ProviderError):
                providers.default_selection()

    def test_ai_provider_forces_choice_over_fireworks(self):
        env = {
            "AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "k",
            "FIREWORKS_API_KEY": "k",  # fireworks usable but must lose
        }
        with mock.patch.dict("os.environ", env):
            pid, model = providers.default_selection()
        self.assertEqual(pid, "openai")
        self.assertEqual(model, "gpt-4o-mini")

    def test_ai_provider_uses_its_model_env(self):
        env = {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "k", "OPENAI_MODEL": "x"}
        with mock.patch.dict("os.environ", env):
            pid, model = providers.default_selection()
        self.assertEqual((pid, model), ("openai", "x"))

    def test_ai_provider_unknown_raises(self):
        with mock.patch.dict("os.environ", {"AI_PROVIDER": "nope"}):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.default_selection()
            self.assertIn("AI_PROVIDER=nope", str(ctx.exception))

    def test_ai_provider_without_key_raises_with_hint(self):
        env = {"AI_PROVIDER": "featherless", "FEATHERLESS_API_KEY": ""}
        with mock.patch.dict("os.environ", env):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.default_selection()
            self.assertIn("FEATHERLESS_API_KEY", str(ctx.exception))

    def test_ai_provider_without_model_raises_with_hint(self):
        env = {
            "AI_PROVIDER": "featherless",
            "FEATHERLESS_API_KEY": "k",
            "FEATHERLESS_MODEL": "",
        }
        with mock.patch.dict("os.environ", env):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.default_selection()
            self.assertIn("FEATHERLESS_MODEL", str(ctx.exception))


class TestAgentSetModel(unittest.TestCase):
    def test_set_model_switches_client_model_and_compressor(self):
        a = StubAgent()
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": "k"}):
            a.set_model("featherless", "some/model-instruct")
        self.assertEqual(a.provider_id, "featherless")
        self.assertEqual(a.model, "some/model-instruct")
        # featherless has no separate vision model -> falls back to chat model
        self.assertEqual(a.vision_model, "some/model-instruct")
        self.assertIn("featherless.ai", str(a.client.base_url))
        self.assertEqual(a.compressor.model, "some/model-instruct")
        self.assertIs(a.compressor.client, a.client)

    def test_set_model_resets_compat(self):
        a = StubAgent()
        a.compat.token_param = "max_completion_tokens"  # learned for old model
        old_compat = a.compat
        with mock.patch.dict("os.environ", {"FEATHERLESS_API_KEY": "k"}):
            a.set_model("featherless", "some/model-instruct")
        self.assertIsNot(a.compat, old_compat)
        self.assertEqual(a.compat.token_param, "max_tokens")  # relearns fresh
        self.assertIs(a.compressor.compat, a.compat)

    def test_set_model_fireworks_keeps_vision_model(self):
        a = StubAgent()
        with mock.patch.dict("os.environ", {"FIREWORKS_API_KEY": "k"}):
            with mock.patch.object(
                providers.config, "FIREWORKS_VISION_MODEL", "fw-vision"
            ):
                a.set_model("fireworks", "fw-chat")
        self.assertEqual(a.model, "fw-chat")
        self.assertEqual(a.vision_model, "fw-vision")

    def test_set_model_unconfigured_raises(self):
        a = StubAgent()
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaises(providers.ProviderError):
                a.set_model("openai", "gpt-4o-mini")

    def test_set_model_unknown_provider_raises(self):
        a = StubAgent()
        with self.assertRaises(providers.ProviderError):
            a.set_model("nope", "m")

    def test_set_model_empty_model_raises(self):
        a = StubAgent()
        with self.assertRaises(ValueError):
            a.set_model("fireworks", "   ")


if __name__ == "__main__":
    unittest.main()
