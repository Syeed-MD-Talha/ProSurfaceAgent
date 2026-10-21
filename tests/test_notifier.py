"""Tests for the owner email notifier and its hooks (no real email sent)."""

import tempfile
import unittest
from unittest.mock import patch

from prosurface_agent.agent import Agent
from prosurface_agent.business import (
    BookingManager,
    OwnerAskTool,
    OwnerNotifier,
    scan_contact_info,
)


class FakeSMTP:
    """Stand-in for smtplib.SMTP; captures what would have been sent."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.login_args = None
        self.sent_message = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent_message = msg

    def quit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RecordingNotifier:
    """Notifier double that records alerts instead of emailing them."""

    enabled = True

    def __init__(self):
        self.sent = []

    def notify(self, subject, body, block=False):
        self.sent.append((subject, body))
        return True


def _make_notifier(**overrides):
    settings = {
        "owner_email": "owner@example.com",
        "smtp_user": "bot@gmail.com",
        "smtp_password": "secret-app-password",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
    }
    settings.update(overrides)
    return OwnerNotifier(**settings)


class TestOwnerNotifier(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_disabled_without_settings(self):
        notifier = OwnerNotifier(owner_email="", smtp_user="", smtp_password="")
        self.assertFalse(notifier.enabled)
        with patch("smtplib.SMTP", side_effect=AssertionError("must not send")):
            self.assertFalse(notifier.notify("Hi", "Body", block=True))
        self.assertEqual(FakeSMTP.instances, [])

    def test_sends_email_with_settings(self):
        notifier = _make_notifier()
        self.assertTrue(notifier.enabled)
        with patch("smtplib.SMTP", FakeSMTP):
            self.assertTrue(notifier.notify("Test subject", "Hello body", block=True))
        self.assertEqual(len(FakeSMTP.instances), 1)
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.host, "smtp.gmail.com")
        self.assertEqual(smtp.port, 587)
        self.assertTrue(smtp.starttls_called)
        self.assertEqual(smtp.login_args, ("bot@gmail.com", "secret-app-password"))
        msg = smtp.sent_message
        self.assertEqual(msg["To"], "owner@example.com")
        self.assertEqual(msg["Subject"], "Test subject")
        self.assertIn("Hello body", msg.get_content())

    def test_send_failure_never_raises(self):
        notifier = _make_notifier()
        with patch("smtplib.SMTP", side_effect=OSError("server down")):
            # block=True runs synchronously; error must be swallowed.
            self.assertTrue(notifier.notify("X", "Y", block=True))


class TestScanContactInfo(unittest.TestCase):
    def test_finds_sg_phone_and_email(self):
        found = scan_contact_info("call me at 9123 4567 or email tan@gmail.com")
        self.assertEqual(found["phones"], ["9123 4567"])
        self.assertEqual(found["emails"], ["tan@gmail.com"])

    def test_finds_international_format(self):
        found = scan_contact_info("my number is +6591234567")
        self.assertEqual(found["phones"], ["+6591234567"])

    def test_ignores_plain_text_and_long_digit_runs(self):
        self.assertEqual(scan_contact_info("the price is 500 dollars"), {"phones": [], "emails": []})
        self.assertEqual(scan_contact_info("booking 202610219123"), {"phones": [], "emails": []})

    def test_empty_text(self):
        self.assertEqual(scan_contact_info(""), {"phones": [], "emails": []})


class TestBookingNotifications(unittest.TestCase):
    def test_create_notifies_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifier = RecordingNotifier()
            manager = BookingManager(tmp, notifier=notifier)
            booking = manager.create(name="Mr Tan", phone="91234567", service="marble polishing")
            self.assertEqual(len(notifier.sent), 1)
            subject, body = notifier.sent[0]
            self.assertIn(booking.booking_id, subject)
            self.assertIn("Mr Tan", body)
            self.assertIn("91234567", body)

    def test_update_notifies_when_phone_first_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifier = RecordingNotifier()
            manager = BookingManager(tmp, notifier=notifier)
            booking = manager.create(name="Ms Lim")
            notifier.sent.clear()

            manager.update(booking.booking_id, phone="8123 4567")
            self.assertEqual(len(notifier.sent), 1)
            self.assertIn("8123 4567", notifier.sent[0][1])

            # Phone already known -> no repeat alert.
            manager.update(booking.booking_id, notes="second floor")
            self.assertEqual(len(notifier.sent), 1)

    def test_update_without_phone_stays_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifier = RecordingNotifier()
            manager = BookingManager(tmp, notifier=notifier)
            booking = manager.create(name="Mr Ong")
            notifier.sent.clear()
            manager.update(booking.booking_id, address="Blk 123 Bedok")
            self.assertEqual(notifier.sent, [])

    def test_default_notifier_is_disabled_noop(self):
        # No .env settings in tests: construction must work and stay silent.
        with tempfile.TemporaryDirectory() as tmp:
            manager = BookingManager(tmp)
            manager.create(name="Test")
            manager.update(manager.list_recent(1)[0].booking_id, phone="91234567")


class TestOwnerAskNotification(unittest.TestCase):
    def test_owner_question_triggers_email(self):
        notifier = RecordingNotifier()
        tool = OwnerAskTool(ask_fn=lambda q: "$500", notifier=notifier)
        result = tool.run({"question": "How much for balcony polish?"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(notifier.sent), 1)
        subject, body = notifier.sent[0]
        self.assertIn(result["question_id"], subject)
        self.assertIn("How much for balcony polish?", body)


class TestAgentContactNotification(unittest.TestCase):
    def _bare_agent(self):
        agent = Agent.__new__(Agent)  # bypass __init__ (needs API key)
        agent._seen_contacts = set()
        return agent

    def test_user_message_with_phone_notifies_once(self):
        agent = self._bare_agent()
        notifier = RecordingNotifier()
        with patch("prosurface_agent.agent.get_notifier", return_value=notifier):
            agent._maybe_notify_contact_shared("my number is 91234567")
            agent._maybe_notify_contact_shared("yes, 91234567 that's right")
        self.assertEqual(len(notifier.sent), 1)
        self.assertIn("91234567", notifier.sent[0][1])

    def test_user_message_without_contact_stays_quiet(self):
        agent = self._bare_agent()
        notifier = RecordingNotifier()
        with patch("prosurface_agent.agent.get_notifier", return_value=notifier):
            agent._maybe_notify_contact_shared("how much for vinyl polishing?")
        self.assertEqual(notifier.sent, [])


if __name__ == "__main__":
    unittest.main()
