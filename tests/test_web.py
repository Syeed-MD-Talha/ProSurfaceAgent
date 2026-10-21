"""Web image-upload validation. No server, no API calls."""

import unittest

from prosurface_agent.web import parse_image_data_url, parse_upload_data_url

TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestWebImageValidation(unittest.TestCase):
    def test_valid_png(self):
        b64, mime, name = parse_image_data_url(TINY_PNG, "wood.png")
        self.assertEqual(mime, "image/png")
        self.assertEqual(name, "wood.png")
        self.assertTrue(len(b64) > 0)

    def test_bad_mime_rejected(self):
        with self.assertRaises(ValueError):
            parse_image_data_url("data:text/plain;base64,aGk=", "x.txt")

    def test_missing_payload_rejected(self):
        with self.assertRaises(ValueError):
            parse_image_data_url("data:image/png;base64,", "x.png")

    def test_oversize_rejected(self):
        with self.assertRaises(ValueError):
            parse_image_data_url(
                "data:image/png;base64," + "A" * 100, "x.png", max_b64_chars=10
            )

    def test_filename_sanitized(self):
        _, _, name = parse_image_data_url(TINY_PNG, "../../evil shady name!.png")
        self.assertNotIn("/", name)
        self.assertNotIn(" ", name)


class TestGenericUploadValidation(unittest.TestCase):
    def test_text_file_with_generic_browser_mime(self):
        parsed = parse_upload_data_url(
            "data:application/octet-stream;base64,aGVsbG8=", "notes.txt"
        )
        self.assertEqual(parsed[1:], ("text/plain", "notes.txt", "document"))

    def test_pdf_file(self):
        parsed = parse_upload_data_url(
            "data:application/pdf;base64,JVBERi0=", "quote.pdf"
        )
        self.assertEqual(parsed[1:], ("application/pdf", "quote.pdf", "pdf"))

    def test_image_file(self):
        parsed = parse_upload_data_url(
            "data:image/png;base64,aGVsbG8=", "floor.png"
        )
        self.assertEqual(parsed[1:], ("image/png", "floor.png", "image"))

    def test_unsupported_extension_rejected(self):
        with self.assertRaisesRegex(ValueError, "supported formats"):
            parse_upload_data_url(
                "data:application/octet-stream;base64,aGVsbG8=", "program.exe"
            )

    def test_mime_extension_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            parse_upload_data_url(
                "data:image/png;base64,aGVsbG8=", "notes.txt"
            )

    def test_filename_sanitized(self):
        _, _, name, _ = parse_upload_data_url(
            "data:text/plain;base64,aGVsbG8=", "../../notes file.txt"
        )
        self.assertNotIn("/", name)
        self.assertNotIn(" ", name)


if __name__ == "__main__":
    unittest.main()
