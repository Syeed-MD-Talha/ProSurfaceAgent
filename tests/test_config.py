"""Config sane defaults. No API calls, no credentials needed."""

import unittest

from prosurface_agent import config


class TestConfig(unittest.TestCase):
    def test_fallback_reply_exact(self):
        self.assertEqual(
            config.FALLBACK_REPLY, "I'm little bit busy now i'll answer you later"
        )

    def test_vision_model_default(self):
        self.assertTrue(
            config.FIREWORKS_VISION_MODEL.endswith("glm-5p3-flash"),
            f"unexpected default: {config.FIREWORKS_VISION_MODEL}",
        )

    def test_limits_positive(self):
        self.assertGreater(config.MODEL_MAX_TOKENS, 0)
        self.assertGreater(config.MAX_IMAGE_BYTES, 0)
        self.assertIn("image/png", config.ALLOWED_IMAGE_MIMES)

    def test_paths_resolve(self):
        self.assertTrue(str(config.project_root()))
        self.assertTrue(str(config.kb_pages_dir()))


if __name__ == "__main__":
    unittest.main()
