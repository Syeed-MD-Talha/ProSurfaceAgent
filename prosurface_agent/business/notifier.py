"""Email notifications to the business owner.

The bot pings the owner by email when:
  - a new booking is created (booking tool)
  - a customer shares a phone number / email in chat
  - the bot does not know an answer and needs the owner (ask_owner tool)

Settings come from .env via config (OWNER_NOTIFY_EMAIL, SMTP_USER,
SMTP_PASSWORD, optional SMTP_HOST / SMTP_PORT). If they are missing the
notifier is disabled and every call becomes a harmless no-op, so the bot
keeps working exactly as before.

Sending happens on a daemon thread by default so a slow mail server never
delays the customer's reply. All send errors are logged, never raised.
"""

from __future__ import annotations

import re
import smtplib
import threading
from email.message import EmailMessage
from typing import Dict, List, Optional

from prosurface_agent import config


class OwnerNotifier:
    """Sends plain-text email alerts to the owner via SMTP."""

    def __init__(
        self,
        owner_email: Optional[str] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
    ) -> None:
        # Explicit arguments win (used by tests); otherwise read .env via config.
        self.owner_email = owner_email if owner_email is not None else config.OWNER_NOTIFY_EMAIL
        self.smtp_user = smtp_user if smtp_user is not None else config.SMTP_USER
        self.smtp_password = smtp_password if smtp_password is not None else config.SMTP_PASSWORD
        self.smtp_host = smtp_host if smtp_host is not None else config.SMTP_HOST
        self.smtp_port = smtp_port if smtp_port is not None else config.SMTP_PORT

    @property
    def enabled(self) -> bool:
        return bool(self.owner_email and self.smtp_user and self.smtp_password)

    def notify(self, subject: str, body: str, block: bool = False) -> bool:
        """Queue an alert email. Returns True if it was (or will be) sent.

        block=True sends synchronously (used by tests); the default sends on
        a background daemon thread so the chat reply is never delayed.
        """
        if not self.enabled:
            print(f"[notifier] skipped (email not configured): {subject}")
            return False
        if block:
            self._send_safe(subject, body)
        else:
            threading.Thread(
                target=self._send_safe, args=(subject, body), daemon=True
            ).start()
        return True

    def send_confirmed(self, subject: str, body: str) -> bool:
        """Report SMTP acceptance for the durable owner-question mail queue."""
        if not self.enabled:
            return False
        return self._send_safe(subject, body)

    def _send_safe(self, subject: str, body: str) -> bool:
        try:
            self._send(subject, body)
            print(f"[notifier] sent: {subject}")
            return True
        except Exception as exc:  # never let email trouble break the bot
            print(f"[notifier] FAILED to send '{subject}': {exc}")
            return False

    def _send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = f"ProSurface Bot <{self.smtp_user}>"
        msg["To"] = self.owner_email
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)


_default_notifier: Optional[OwnerNotifier] = None


def get_notifier() -> OwnerNotifier:
    """Process-wide notifier built from .env settings (lazy singleton)."""
    global _default_notifier
    if _default_notifier is None:
        _default_notifier = OwnerNotifier()
    return _default_notifier


# --- Contact-detail scanning -------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Singapore-style numbers: 8 digits starting 6/8/9, optional +65 / 65 prefix,
# optional space or dash in the middle. Lookarounds avoid matching inside
# longer digit runs (e.g. dates or IDs).
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?65[- ]?)?[689]\d{3}[- ]?\d{4}(?!\d)")


def scan_contact_info(text: str) -> Dict[str, List[str]]:
    """Find phone numbers and email addresses in a customer message."""
    if not text:
        return {"phones": [], "emails": []}
    emails = sorted(set(_EMAIL_RE.findall(text)))
    phones = sorted({m.group(0).strip() for m in _PHONE_RE.finditer(text)})
    return {"phones": phones, "emails": emails}
