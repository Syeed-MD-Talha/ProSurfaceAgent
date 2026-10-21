"""Web chat request limits; no server, customer data, or API calls."""

import unittest
from datetime import datetime, timedelta, timezone

from prosurface_agent.web import ChatRateLimiter


class FakeClock:
    def __init__(self):
        self.monotonic = 0.0
        self.utc = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.monotonic += seconds
        self.utc += timedelta(seconds=seconds)


class TestChatRateLimiter(unittest.TestCase):
    def limiter(self, per_minute=2, daily=3):
        clock = FakeClock()
        limiter = ChatRateLimiter(
            per_minute,
            daily,
            monotonic=lambda: clock.monotonic,
            utcnow=lambda: clock.utc,
        )
        return limiter, clock

    def test_per_session_minute_limit_and_retry_time(self):
        limiter, clock = self.limiter(per_minute=2)
        self.assertIsNone(limiter.check("session-a"))
        self.assertIsNone(limiter.check("session-a"))
        self.assertEqual(limiter.check("session-a"), 60)

        clock.advance(11)
        self.assertEqual(limiter.check("session-a"), 49)
        self.assertIsNone(limiter.check("session-b"))

    def test_daily_cap_is_shared_across_sessions_and_resets_utc(self):
        limiter, clock = self.limiter(per_minute=10, daily=2)
        self.assertIsNone(limiter.check("session-a"))
        self.assertIsNone(limiter.check("session-b"))
        self.assertEqual(limiter.check("session-c"), 12 * 60 * 60)

        clock.advance(12 * 60 * 60)
        self.assertIsNone(limiter.check("session-c"))

    def test_missing_session_is_not_admitted(self):
        limiter, _ = self.limiter()
        self.assertEqual(limiter.check(None), 1)

    def test_logout_discards_session_minute_history(self):
        limiter, _ = self.limiter(per_minute=1)
        self.assertIsNone(limiter.check("session-a"))
        self.assertEqual(limiter.check("session-a"), 60)
        limiter.forget_session("session-a")
        self.assertIsNone(limiter.check("session-a"))


if __name__ == "__main__":
    unittest.main()
