"""The redesigned templates retain the existing login boundary."""

import unittest

from prosurface_agent.web import Handler, LOGIN_PAGE, PAGE


class TestPageAccess(unittest.TestCase):
    def render(self, authorized):
        handler = Handler.__new__(Handler)
        handler.path = "/"
        handler._authorized = lambda: authorized
        sent = []
        handler._send = lambda status, body, content_type: sent.append(
            (status, body.decode("utf-8"), content_type)
        )
        handler.do_GET()
        return sent[0]

    def test_chat_template_requires_sign_in(self):
        status, body, content_type = self.render(False)
        self.assertEqual(status, 200)
        self.assertEqual(body, LOGIN_PAGE)
        self.assertNotIn('id="composer"', body)
        self.assertEqual(content_type, "text/html; charset=utf-8")

    def test_authenticated_session_receives_chat_template(self):
        status, body, content_type = self.render(True)
        self.assertEqual(status, 200)
        self.assertEqual(body, PAGE)
        self.assertIn('id="composer"', body)
        self.assertEqual(content_type, "text/html; charset=utf-8")
