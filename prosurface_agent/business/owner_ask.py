"""Service-question escalation: durable email reply links for web chats.

The terminal entry point retains its interactive owner prompt. Web callers
provide a store and chat ID, enqueue a question, and return immediately.
"""

from __future__ import annotations

import threading
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .notifier import OwnerNotifier, get_notifier


_SERVICE = re.compile(
    r"\b(floor\w*|marble|parquet|wood\w*|timber|tile\w*|grout\w*|stone|granite|"
    r"terrazzo|vinyl|deck\w*|polish\w*|sand\w*|varnish\w*|epoxy|carpet|"
    r"cabinet\w*|door\w*|repair\w*|restor\w*|stain\w*|scratch\w*|"
    r"prosurface|pro surface|quot\w*|pric\w*|cost\w*|warranty|warranties|"
    r"book\w*|appointment|availab\w*|worker\w*|crew|slot\w*|deposit|invoice|"
    r"payment|refund|service\w*|colour\w*|color\w*|finish\w*|installation)\b", re.I
)
_OFF_TOPIC = re.compile(r"\b(weather|football|cricket|match|president|politic\w*|recipe|joke|code|python|homework|stock|bitcoin)\b", re.I)
_SURFACE = re.compile(r"\b(floor\w*|marble|parquet|timber|tile\w*|grout\w*|terrazzo|vinyl|deck\w*|stone|cabinet\w*)\b", re.I)
_FOLLOW_UP = re.compile(r"\b(it|that|this|they|those|you|your|team|when|weekend|saturday|sunday|tomorrow)\b", re.I)


def is_service_question(text: str, messages: list) -> bool:
    """Conservative extra check; prompt routing also rejects unrelated requests."""
    if _OFF_TOPIC.search(text) and not _SURFACE.search(text):
        return False
    if _SERVICE.search(text):
        return True
    previous = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
    # A short follow-up may refer to a service in the customer's previous turn.
    if previous and previous[-1].strip() == text.strip():
        previous = previous[:-1]
    return bool(len(text) < 400 and _FOLLOW_UP.search(text)
                and previous and _SERVICE.search(previous[-1]))


@dataclass
class OwnerQuestion:
    question_id: str
    client_question: str
    context: str
    status: str = "pending"  # pending / answered / dismissed
    answer: str = ""
    asked_at: str = ""
    answered_at: str = ""


class OwnerAskTool:
    """Owner escalation with optional durable, nonblocking web delivery."""

    def __init__(
        self,
        ask_fn: Optional[Callable[[str], str]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        notifier: Optional[OwnerNotifier] = None,
        store=None,
        chat_id: Optional[str] = None,
    ) -> None:
        self.questions: Dict[str, OwnerQuestion] = {}
        self.ask_fn = ask_fn or self._terminal_ask
        self.messages = messages if messages is not None else []
        self.store = store
        self.chat_id = chat_id
        self._notifier = notifier if notifier is not None else get_notifier()
        self._lock = threading.Lock()

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "ask_owner",
                "description": (
                    "Use ONLY for unanswered questions related to ProSurface floor services. "
                    "Examples: unknown price, custom scope, color match not in catalog, "
                    "worker availability, or unusual service requests. Never use for unrelated topics. "
                    "In web chat this records a pending request and emails the owner a private reply link. "
                    "Use the returned customer_reply; never pretend a pending question is answered."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The exact question to ask the owner.",
                        },
                        "context": {
                            "type": "string",
                            "description": "Background context (client request, booking id, photos, etc.).",
                        },
                    },
                    "required": ["question"],
                },
            },
        }

    def run(self, arguments: dict) -> dict:
        question = str(arguments.get("question") or "").strip()
        if not question:
            return {"ok": False, "error": "question is required"}

        # Use the actual last user message as the client question, not the LLM's rewrite.
        actual_question = self._last_user_message() or question

        # Build context from actual conversation messages, not the LLM's summary.
        real_context = self._build_context_from_messages()

        if self.store is not None:
            from prosurface_agent import config

            if not is_service_question(actual_question, self.messages):
                return {"ok": False, "status": "unrelated", "customer_reply": config.FALLBACK_REPLY}
            if not self._notifier.enabled:
                return {
                    "ok": False, "status": "unavailable",
                    "customer_reply": "Boss I need our team to confirm this. Please contact ProSurface directly for confirmation.",
                }
            queued = self.store.queue_question(
                self.chat_id, actual_question, question, real_context,
                config.OWNER_REPLY_TOKEN_TTL_HOURS,
            )
            if queued.get("delivery") == "failed" and queued.get("attempts", 0) >= 3:
                return {"ok": False, "status": "unavailable", "customer_reply": "Boss I couldn't reach our team. Please contact ProSurface directly for confirmation."}
            return {
                "ok": True, "question_id": queued["id"], "status": "pending",
                "customer_reply": "Boss I'll check with our team and update you here once they confirm. You can return to this chat for the answer.",
            }

        qid = f"OQ-{uuid.uuid4().hex[:8].upper()}"
        owner_q = OwnerQuestion(
            question_id=qid,
            client_question=actual_question,
            context=real_context,
            asked_at=_now(),
        )
        with self._lock:
            self.questions[qid] = owner_q

        prompt = self._build_prompt(owner_q)
        self._notifier.notify(
            f"[ProSurface] Bot needs your answer ({qid})",
            "The chat bot could not answer a customer and is asking for you.\n\n"
            + prompt
            + "\n\n(One-way alert for now — replying to this email does not reach the bot.)",
        )
        answer = self.ask_fn(prompt)

        with self._lock:
            owner_q.answer = answer.strip()
            owner_q.status = "answered" if owner_q.answer else "dismissed"
            owner_q.answered_at = _now()

        return {
            "ok": bool(owner_q.answer),
            "question_id": qid,
            "status": owner_q.status,
            "answer": owner_q.answer,
            "note": (
                "Owner answered. Relay this to the client in WhatsApp style."
                if owner_q.answer else "No owner answer was received. Do not invent an answer."
            ),
        }

    def list_pending(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "question_id": q.question_id,
                    "client_question": q.client_question,
                    "context": q.context,
                    "asked_at": q.asked_at,
                }
                for q in self.questions.values()
                if q.status == "pending"
            ]

    @staticmethod
    def _terminal_ask(prompt: str) -> str:
        print("\n" + "=" * 60)
        print("OWNER QUESTION — please answer to continue")
        print("=" * 60)
        print(prompt)
        print("-" * 60)
        try:
            return input("Owner answer: ").strip()
        except EOFError:
            return ""

    def _last_user_message(self) -> Optional[str]:
        """Return the most recent user message content."""
        for m in reversed(self.messages):
            if m.get("role") == "user" and m.get("content"):
                return str(m["content"]).strip()
        return None

    def _build_context_from_messages(self) -> str:
        """Use the last few real messages; ignore any fabricated details."""
        lines = []
        recent = [m for m in self.messages if m.get("role") in ("user", "assistant")][-6:]
        if recent:
            lines.append("Recent conversation:")
            for m in recent:
                role = m.get("role", "unknown")
                content = str(m.get("content") or "")[:300]
                lines.append(f"- {role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_prompt(q: OwnerQuestion) -> str:
        lines = [
            f"Question ID: {q.question_id}",
            f"Client asked: {q.client_question}",
        ]
        if q.context:
            lines.append(f"Context:\n{q.context}")
        lines.append("Reply with the answer the agent should give the client.")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
