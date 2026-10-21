"""Environment overrides for web chat rate limits."""

import os
import unittest
from unittest.mock import patch

from prosurface_agent.config import _positive_int_env


class TestPositiveIntEnvironmentSetting(unittest.TestCase):
    def test_reads_positive_override(self):
        with patch.dict(os.environ, {"QA_TEST_LIMIT": "42"}):
            self.assertEqual(_positive_int_env("QA_TEST_LIMIT", 7), 42)

    def test_invalid_or_nonpositive_values_use_default(self):
        for value in ("invalid", "0", "-2"):
            with self.subTest(value=value), patch.dict(os.environ, {"QA_TEST_LIMIT": value}):
                self.assertEqual(_positive_int_env("QA_TEST_LIMIT", 7), 7)


if __name__ == "__main__":
    unittest.main()
