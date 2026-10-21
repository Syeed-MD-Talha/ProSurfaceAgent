"""Deliver durable owner requests without holding a customer's HTTP request."""

import threading
from urllib.parse import urlsplit


def reply_base_url(configured: str, port: int) -> str:
    base = configured or f"http://127.0.0.1:{port}"
    parsed = urlsplit(base)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("OWNER_REPLY_BASE_URL must be the website origin, e.g. https://chat.example.com")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("OWNER_REPLY_BASE_URL must use HTTPS for a hosted website")
    return base.rstrip("/")


class OwnerMailWorker:
    def __init__(self, store, notifier, base_url):
        self.store = store
        self.notifier = notifier
        self.base_url = base_url
        self.stop_event = threading.Event()

    def deliver_one(self) -> bool:
        if not self.notifier.enabled:
            return False
        question = self.store.claim_email()
        if not question:
            return False
        link = f"{self.base_url}/owner/reply#token={question['token']}"
        body = (
            f"A customer needs your confirmation.\n\nQuestion ID: {question['id']}\n"
            f"Customer's message:\n{question['client_question']}\n\n"
            f"What the bot needs confirmed:\n{question['owner_question']}\n\n"
            f"Recent conversation:\n{question['context']}\n\n"
            f"Open this private link to write the answer to send to this customer:\n{link}\n\n"
            "The answer will appear in that customer's chat as written. The link expires "
            "and can submit one answer. Keep it private. Replying directly to this email "
            "does not submit an answer; use the link above."
        )
        try:
            sent = self.notifier.send_confirmed(
                f"[ProSurface] Customer needs your answer ({question['id']})", body
            )
        except Exception:
            sent = False
        self.store.email_result(question["id"], bool(sent))
        return True

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    if self.deliver_one():
                        continue
                except Exception:
                    # SQLite/SMTP failures must not terminate the worker.
                    print("[owner mail] delivery temporarily unavailable")
                self.stop_event.wait(3)
        threading.Thread(target=run, name="owner-mail", daemon=True).start()

    def stop(self):
        self.stop_event.set()
