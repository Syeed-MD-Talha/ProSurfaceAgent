"""Vision-failure honesty: the note must say the photo arrived.

Stubbed Agent (no __init__, no API calls) exercising chat_once's
image-failure branch.
"""

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from prosurface_agent.agent import Agent


class StubAgent(Agent):
    def __init__(self, workspace):
        # Bypass __init__ (would exit without API key / hit network).
        self.session_id = "test-session"
        self.messages = []
        self.workspace_dir = Path(workspace)
        self.captured_note = ""

    def ensure_session(self):
        pass

    def describe_image(self, image_base64, mime="image/jpeg"):
        return "[image could not be analysed: Error code: 404]"

    def _add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
        if role == "user":
            self.captured_note = content

    def _maybe_compress(self):
        pass

    def _respond_sync(self):
        return "STUB-REPLY"


class TestVisionFailPath(unittest.TestCase):
    def test_failure_note_says_photo_arrived(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = StubAgent(tmp)
            raw = b"fake-image-bytes"
            reply = agent.chat_once(
                "Can you classify this wood?",
                base64.b64encode(raw).decode(),
                "image/png",
                "wood.png",
            )
            self.assertEqual(reply, "STUB-REPLY")
            self.assertIn("Photo received", agent.captured_note)
            self.assertNotIn("didn't come through", agent.captured_note)

    def test_uploaded_text_document_is_added_to_user_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = StubAgent(tmp)
            reply = agent.chat_once(
                "Please read this quote",
                document_b64=base64.b64encode(b"Marble polishing: $450").decode(),
                document_filename="quote.txt",
            )
            self.assertEqual(reply, "STUB-REPLY")
            self.assertIn("Document attached: quote.txt", agent.captured_note)
            self.assertIn("Marble polishing: $450", agent.captured_note)

    def test_uploads_off_by_default(self):
        # Privacy default: customer photos are not written to disk.
        with tempfile.TemporaryDirectory() as tmp:
            agent = StubAgent(tmp)
            agent.chat_once(
                "hi",
                base64.b64encode(b"fake-image-bytes").decode(),
                "image/png",
                "wood.png",
            )
            self.assertFalse((Path(tmp) / "uploads" / "wood.png").exists())

    def test_uploads_saved_when_enabled(self):
        from prosurface_agent import config as config_module

        with tempfile.TemporaryDirectory() as tmp:
            agent = StubAgent(tmp)
            old, config_module.SAVE_UPLOADS = config_module.SAVE_UPLOADS, True
            try:
                agent.chat_once(
                    "hi",
                    base64.b64encode(b"fake-image-bytes").decode(),
                    "image/png",
                    "wood.png",
                )
            finally:
                config_module.SAVE_UPLOADS = old
            self.assertTrue((Path(tmp) / "uploads" / "wood.png").exists())

    def test_empty_turn_greets(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = StubAgent(tmp)
            self.assertIn("boss", agent.chat_once(""))


class PriceQuoteStubAgent(Agent):
    """Bypass __init__ — exercises _inject_price_quote only."""

    def __init__(self):
        self.messages = []
        self.session_id = None
        self.pricing_tool = SimpleNamespace(
            run=lambda args: {"quotes": [], "note": "from $3.50 psf"}
        )

    def _persist_message(self, *args, **kwargs):
        pass


class TestPriceQuoteInjection(unittest.TestCase):
    def test_injected_sequence_is_api_valid(self):
        # OpenAI-compatible APIs reject a tool message that is not preceded
        # by an assistant message with matching tool_calls.
        agent = PriceQuoteStubAgent()
        agent._inject_price_quote("how much for marble?")
        self.assertEqual(len(agent.messages), 2)
        assistant, tool = agent.messages
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["tool_calls"][0]["id"], "auto-price-quote")
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "price_quote")
        self.assertEqual(tool["role"], "tool")
        self.assertEqual(tool["tool_call_id"], "auto-price-quote")
        self.assertEqual(tool["name"], "price_quote")


if __name__ == "__main__":
    unittest.main()
