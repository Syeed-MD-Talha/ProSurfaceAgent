"""Owner follow-up acceptance tests with private temporary state and fake mail.

The real HTTP handlers, persistence, token checks and customer isolation are
exercised in memory. No network, provider calls, or live customer data.
"""

import io
import json
import re
import tempfile
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from prosurface_agent import config, web
from prosurface_agent.agent import Agent
from prosurface_agent.business.owner_ask import OwnerAskTool, is_service_question
from prosurface_agent.business.owner_mail import OwnerMailWorker, reply_base_url
from prosurface_agent.web_state import WebState


class FakeMail:
    enabled = True

    def __init__(self):
        self.sent = []
        self.succeeds = True

    def send_confirmed(self, subject, body):
        self.sent.append((subject, body))
        return self.succeeds


class FakeAgent:
    calls = 0

    def __init__(self, session_store=None):
        self.messages = []

    def ensure_session(self):
        self.messages.append({"role": "system", "content": "Current prompt"})

    def _persist_message(self, *args, **kwargs):
        pass

    def chat_once(self, message, *args):
        FakeAgent.calls += 1
        self.messages.append({"role": "user", "content": message})
        reply = "Hello boss, how can we help with your floor?"
        if message != "hello":
            result = self.owner_ask_tool.run({"question": message})
            reply = result["customer_reply"]
        self.messages.append({"role": "assistant", "content": reply})
        return reply


class TestOwnerWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = WebState(self.directory)
        self.mail = FakeMail()
        self.worker = OwnerMailWorker(self.store, self.mail, "https://chat.example.com")
        FakeAgent.calls = 0
        self.patches = [
            patch.object(web, "_state", self.store),
            patch.object(web, "_agents", OrderedDict()),
            patch.object(web, "_sessions", set()),
            patch.object(web, "_chat_rate_limiter", web.ChatRateLimiter(30, 200)),
            patch.object(config, "web_state_dir", return_value=self.directory),
            patch.object(config, "WEB_LOGIN_USERNAME", "test-user"),
            patch.object(config, "WEB_LOGIN_PASSWORD", "test-password"),
            patch.object(config, "OWNER_REPLY_BASE_URL", ""),
            patch("prosurface_agent.agent.Agent", FakeAgent),
            patch("prosurface_agent.sessions.SessionStore", return_value=object()),
            patch("prosurface_agent.business.owner_ask.get_notifier", return_value=self.mail),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, method, path, payload=None, cookie="", token=""):
        handler = web.Handler.__new__(web.Handler)
        handler.path = path
        raw = json.dumps(payload).encode() if payload is not None else b""
        handler.headers = {"Content-Length": str(len(raw)), "Cookie": cookie}
        if token:
            handler.headers["Authorization"] = "Bearer " + token
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        headers, status = [], []
        handler.send_response = status.append
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        getattr(handler, "do_" + method)()
        data = handler.wfile.getvalue().decode()
        return status[0], json.loads(data), headers

    def login(self, previous=""):
        status, _, headers = self.request("POST", "/api/login", {
            "username": "test-user", "password": "test-password",
        }, cookie=previous)
        self.assertEqual(status, 200)
        return "; ".join(value.split(";", 1)[0] for key, value in headers if key == "Set-Cookie")

    def ask(self, cookie, message="Can you restore a custom terrazzo colour?", request_id="request-one"):
        return self.request("POST", "/api/chat", {"message": message, "request_id": request_id}, cookie=cookie)

    def mail_token(self):
        self.assertTrue(self.worker.deliver_one())
        return re.search(r"/owner/reply#token=([A-Za-z0-9_-]+)", self.mail.sent[-1][1]).group(1)

    def test_owner_answer_reaches_only_original_chat_and_survives_restart(self):
        customer_a, customer_b = self.login(), self.login()
        status, reply, _ = self.ask(customer_a)
        self.assertEqual(status, 200)
        self.assertIn("check with our team", reply["reply"])
        token = self.mail_token()
        status, question, headers = self.request("GET", "/api/owner/question", token=token)
        self.assertEqual(status, 200)
        self.assertIn("custom terrazzo", question["question"]["client_question"])
        self.assertIn(("Cache-Control", "no-store"), headers)
        answer = "Yes boss, our team can assess the terrazzo colour on site first."
        self.assertEqual(self.request("POST", "/api/owner/answer", {"answer": answer}, token=token)[0], 200)
        self.assertEqual(self.request("POST", "/api/owner/answer", {"answer": "replacement"}, token=token)[0], 409)

        a_history = self.request("GET", "/api/chat/history", cookie=customer_a)[1]
        b_history = self.request("GET", "/api/chat/history", cookie=customer_b)[1]
        self.assertEqual([m["content"] for m in a_history["messages"] if m["source"] == "owner"], [answer])
        self.assertEqual(a_history["pending"], [])
        self.assertEqual(b_history["messages"], [])

        # New process state, same private browser cookie; sign in after restart.
        web._agents.clear()
        web._sessions.clear()
        web._state = WebState(self.directory)
        customer_a = self.login(customer_a)
        restored = self.request("GET", "/api/chat/history", cookie=customer_a)[1]
        self.assertEqual(len(restored["messages"]), 3)
        chat_id = self.store.chat_for_token(web.parse_session_cookie(customer_a, web.CHAT_COOKIE))
        agent = web.get_agent(chat_id)
        self.assertTrue(any(answer in m.get("content", "") for m in agent.messages))

    def test_retried_message_is_not_billed_or_emailed_twice(self):
        customer = self.login()
        first = self.ask(customer)[1]
        retry = self.ask(customer)[1]
        self.assertEqual(FakeAgent.calls, 1)
        self.assertEqual(first, retry)
        history = self.request("GET", "/api/chat/history", cookie=customer)[1]
        self.assertEqual(len(history["messages"]), 2)
        self.assertEqual(len(history["pending"]), 1)
        self.mail_token()
        self.assertFalse(self.worker.deliver_one())

    def test_same_pending_question_reuses_owner_request(self):
        customer = self.login()
        self.ask(customer, request_id="request-one")
        self.ask(customer, request_id="request-two")
        history = self.request("GET", "/api/chat/history", cookie=customer)[1]
        self.assertEqual(len(history["pending"]), 1)

    def test_unrelated_question_never_queues_mail(self):
        customer = self.login()
        reply = self.ask(customer, "Who won the football match?")[1]
        self.assertEqual(reply["reply"], config.FALLBACK_REPLY)
        self.assertFalse(self.worker.deliver_one())

    def test_unconfigured_email_does_not_promise_owner_notification(self):
        self.mail.enabled = False
        customer = self.login()
        reply = self.ask(customer)[1]
        self.assertIn("contact ProSurface directly", reply["reply"])
        self.assertEqual(self.request("GET", "/api/chat/history", cookie=customer)[1]["pending"], [])

    def test_missing_wrong_and_expired_tokens_are_rejected(self):
        customer = self.login()
        self.ask(customer)
        token = self.mail_token()
        for wrong in ("", "wrong-token"):
            self.assertEqual(self.request("GET", "/api/owner/question", token=wrong)[0], 404)
            self.assertEqual(self.request("POST", "/api/owner/answer", {"answer": "Yes"}, token=wrong)[0], 404)
        with patch("prosurface_agent.web_state.time.time", return_value=time.time() + 8 * 86400):
            self.assertEqual(self.request("GET", "/api/owner/question", token=token)[0], 404)
            self.assertEqual(self.request("POST", "/api/owner/answer", {"answer": "Yes"}, token=token)[0], 404)

    def test_history_requires_login_and_private_chat_cookie(self):
        self.assertEqual(self.request("GET", "/api/chat/history")[0], 401)
        customer = self.login()
        auth_only = customer.split(";", 1)[0]
        self.assertEqual(self.request("GET", "/api/chat/history", cookie=auth_only)[0], 409)
        self.assertEqual(self.ask(auth_only)[0], 409)

    def test_rate_limit_applies_before_recording_or_model_call_and_allows_replay(self):
        web._chat_rate_limiter = web.ChatRateLimiter(1, 200)
        customer = self.login()
        self.ask(customer, "hello", "first-message")
        self.assertEqual(self.ask(customer, "hello", "second-message")[0], 429)
        self.assertEqual(self.ask(customer, "hello", "first-message")[0], 200)
        self.assertEqual(FakeAgent.calls, 1)
        self.assertEqual(len(self.request("GET", "/api/chat/history", cookie=customer)[1]["messages"]), 2)

    def test_failed_email_retries_after_restart_with_fresh_private_link(self):
        customer = self.login()
        self.ask(customer)
        self.mail.succeeds = False
        old_token = self.mail_token()
        self.mail.succeeds = True
        with patch("prosurface_agent.web_state.time.time", return_value=time.time() + 61):
            restarted = OwnerMailWorker(WebState(self.directory), self.mail, "https://chat.example.com")
            self.assertTrue(restarted.deliver_one())
        new_token = re.search(r"#token=([A-Za-z0-9_-]+)", self.mail.sent[-1][1]).group(1)
        self.assertNotEqual(old_token, new_token)
        self.assertIsNone(self.store.owner_question(old_token))
        self.assertIsNotNone(self.store.owner_question(new_token))

    def test_pending_request_survives_worker_interruption(self):
        self.ask(self.login())
        first = self.store.claim_email()
        self.assertIsNotNone(first)
        restarted = WebState(self.directory)
        second = restarted.claim_email()
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["token"], second["token"])

    def test_interrupted_last_mail_attempt_is_reported_as_delivery_failure(self):
        cookie = self.login()
        self.ask(cookie)
        for _ in range(3):
            self.assertIsNotNone(self.store.claim_email())
            self.store = WebState(self.directory)
        self.assertIsNone(self.store.claim_email())
        chat_id = self.store.chat_for_token(web.parse_session_cookie(cookie, web.CHAT_COOKIE))
        self.assertEqual(self.store.pending(chat_id)[0]["delivery"], "failed")


class TestOwnerRouting(unittest.TestCase):
    def test_contextual_service_followup_but_no_sports_escalation(self):
        messages = [{"role": "user", "content": "I need marble polishing"}]
        self.assertTrue(is_service_question("Can your team do it tomorrow?", messages))
        self.assertFalse(is_service_question("Who won the cricket match?", messages))
        self.assertFalse(is_service_question("What is the bitcoin price?", messages))

    def test_initially_empty_messages_reference_is_retained(self):
        messages = []
        tool = OwnerAskTool(messages=messages, ask_fn=lambda _: "Yes", notifier=Mock())
        messages.append({"role": "user", "content": "Actual floor question"})
        self.assertEqual(tool._last_user_message(), "Actual floor question")

    def test_web_tool_completes_model_turn_without_waiting_or_extra_model_call(self):
        agent = Agent.__new__(Agent)
        agent.model, agent.client = "stub", object()
        agent.messages = [{"role": "user", "content": "Custom marble quote?"}]
        agent._get_tool_schemas = lambda: []
        agent._persist_message = lambda *args, **kwargs: None
        agent._execute_tool = lambda *args: {"ok": True, "status": "pending", "customer_reply": "I'll check with our team and update you here."}
        call = SimpleNamespace(id="owner-call", type="function", function=SimpleNamespace(name="ask_owner", arguments='{"question":"Custom marble quote?"}'))
        agent.compat = SimpleNamespace(create=Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))])))
        self.assertIn("check with our team", agent._respond_sync())
        self.assertEqual(agent.compat.create.call_count, 1)
        self.assertEqual(agent.messages[-2]["role"], "tool")
        self.assertEqual(agent.messages[-1]["role"], "assistant")

    def test_hosted_links_require_explicit_https_origin(self):
        self.assertEqual(reply_base_url("", 8123), "http://127.0.0.1:8123")
        self.assertEqual(reply_base_url("https://chat.example.com", 8000), "https://chat.example.com")
        for invalid in ("http://chat.example.com", "https://person:password@example.com", "https://example.com/path", "https://example.com#token=x"):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                reply_base_url(invalid, 8000)


if __name__ == "__main__":
    unittest.main()
