"""Web login/session logic. No server, no API calls."""

import unittest

from prosurface_agent import config
from prosurface_agent.auth import verify_web_login
from prosurface_agent.web import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    end_session,
    is_valid_session,
    parse_session_cookie,
    session_cookie,
)


class TestVerifyWebLogin(unittest.TestCase):
    def setUp(self):
        self._orig = (config.WEB_LOGIN_USERNAME, config.WEB_LOGIN_PASSWORD)
        config.WEB_LOGIN_USERNAME = "admin"
        config.WEB_LOGIN_PASSWORD = "secret123"

    def tearDown(self):
        config.WEB_LOGIN_USERNAME, config.WEB_LOGIN_PASSWORD = self._orig

    def test_correct_credentials(self):
        self.assertTrue(verify_web_login("admin", "secret123"))

    def test_wrong_password(self):
        self.assertFalse(verify_web_login("admin", "wrong"))

    def test_wrong_username(self):
        self.assertFalse(verify_web_login("root", "secret123"))

    def test_empty_credentials(self):
        self.assertFalse(verify_web_login("", ""))

    def test_fail_closed_when_unset(self):
        config.WEB_LOGIN_USERNAME = ""
        config.WEB_LOGIN_PASSWORD = ""
        self.assertFalse(verify_web_login("admin", "secret123"))
        self.assertFalse(verify_web_login("", ""))


class TestSessions(unittest.TestCase):
    def test_create_and_end_session(self):
        token = create_session()
        self.assertTrue(token)
        self.assertTrue(is_valid_session(token))
        end_session(token)
        self.assertFalse(is_valid_session(token))

    def test_invalid_tokens(self):
        self.assertFalse(is_valid_session(None))
        self.assertFalse(is_valid_session(""))
        self.assertFalse(is_valid_session("not-a-real-token"))

    def test_end_session_none_is_noop(self):
        end_session(None)  # must not raise

    def test_tokens_are_unique(self):
        self.assertNotEqual(create_session(), create_session())


class TestCookieParsing(unittest.TestCase):
    def test_extracts_session_token(self):
        header = f"other=x; {SESSION_COOKIE}=abc123; theme=dark"
        self.assertEqual(parse_session_cookie(header), "abc123")

    def test_only_session_cookie(self):
        self.assertEqual(parse_session_cookie(f"{SESSION_COOKIE}=tok"), "tok")

    def test_missing_header(self):
        self.assertIsNone(parse_session_cookie(None))
        self.assertIsNone(parse_session_cookie(""))

    def test_no_session_cookie(self):
        self.assertIsNone(parse_session_cookie("theme=dark; user=1"))

    def test_empty_value_ignored(self):
        self.assertIsNone(parse_session_cookie(f"{SESSION_COOKIE}=; a=b"))

    def test_cookie_strings(self):
        token = "tok123"
        self.assertIn(f"{SESSION_COOKIE}={token}", session_cookie(token))
        self.assertIn("HttpOnly", session_cookie(token))
        self.assertIn("Max-Age=0", clear_session_cookie())


if __name__ == "__main__":
    unittest.main()
