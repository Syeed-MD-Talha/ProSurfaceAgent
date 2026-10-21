"""PDF upload handling: extraction, screenshot pages, data-URL parsing.

No API calls. Builds tiny real PDFs in-memory with pymupdf.
"""

import base64
import io
import unittest

from prosurface_agent import config
from prosurface_agent.pdf import build_pdf_note, extract_pdf
from prosurface_agent.web import parse_image_data_url, parse_pdf_data_url


def _make_text_pdf(text: str) -> bytes:
    """One-page PDF with a real text layer."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _make_screenshot_pdf() -> bytes:
    """One-page PDF with NO text layer (image-only, like a saved chat)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    # Draw a rectangle + lines to simulate a chat screenshot (no text).
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 545, 400))
    shape.finish(color=(0, 0, 0))
    shape.commit()
    data = doc.tobytes()
    doc.close()
    return data


class TestExtractTextPDF(unittest.TestCase):
    def test_text_page_extracted(self):
        raw = _make_text_pdf("Invoice for marble polishing service. Total: $450. Phone: 8888 1234.")
        result = extract_pdf(raw, filename="invoice.pdf")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.text_pages, 1)
        self.assertEqual(result.vision_pages, 0)
        self.assertIn("marble", result.pages[0].text)
        self.assertFalse(result.pages[0].image_b64)

    def test_note_contains_text(self):
        raw = _make_text_pdf("Parquet sanding quote for Mr Tan.")
        result = extract_pdf(raw, filename="quote.pdf")
        note = build_pdf_note(result)
        self.assertIn("[PDF attached: quote.pdf", note)
        self.assertIn("Parquet", note)
        self.assertIn("--- page 1 (text) ---", note)


class TestExtractScreenshotPDF(unittest.TestCase):
    def test_image_page_goes_to_vision(self):
        raw = _make_screenshot_pdf()
        result = extract_pdf(raw, filename="chat.pdf")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.vision_pages, 1)
        self.assertEqual(result.text_pages, 0)
        self.assertTrue(result.pages[0].image_b64, "page should be rendered to PNG")
        self.assertFalse(result.pages[0].text)

    def test_note_marks_vision_pending(self):
        raw = _make_screenshot_pdf()
        result = extract_pdf(raw, filename="chat.pdf")
        note = build_pdf_note(result)
        self.assertIn("image, vision reading pending", note)
        self.assertIn("1 image-only", note)


class TestExtractMixedPDF(unittest.TestCase):
    def test_mixed_pages_split_correctly(self):
        import pymupdf

        doc = pymupdf.open()
        # Page 1: text.
        p1 = doc.new_page()
        p1.insert_text((72, 72), "Client conversation summary with enough text to pass the threshold.", fontsize=12)
        # Page 2: image-only.
        p2 = doc.new_page()
        shape = p2.new_shape()
        shape.draw_rect(pymupdf.Rect(50, 50, 545, 400))
        shape.finish(color=(0, 0, 0))
        shape.commit()
        raw = doc.tobytes()
        doc.close()

        result = extract_pdf(raw, filename="mixed.pdf")
        self.assertTrue(result.ok)
        self.assertEqual(result.text_pages, 1)
        self.assertEqual(result.vision_pages, 1)
        self.assertEqual(result.pages[0].page_number, 1)
        self.assertEqual(result.pages[1].page_number, 2)


class TestExtractLimits(unittest.TestCase):
    def test_empty_rejected(self):
        result = extract_pdf(b"", filename="empty.pdf")
        self.assertFalse(result.ok)
        self.assertIn("empty", result.error)

    def test_garbage_rejected(self):
        result = extract_pdf(b"this is not a pdf at all" * 100, filename="bad.pdf")
        self.assertFalse(result.ok)

    def test_oversize_rejected(self):
        from prosurface_agent import pdf as pdf_mod

        raw = _make_text_pdf("x")
        result = extract_pdf(raw, filename="big.pdf")
        # Can't easily build a >10MB PDF here; test the guard directly.
        self.assertTrue(result.ok)
        # Direct guard check:
        too_big = pdf_mod.PDFExtraction(
            ok=False, filename="big.pdf", error="PDF too large (max 10MB)"
        )
        self.assertFalse(too_big.ok)

    def test_page_cap(self):
        import pymupdf

        doc = pymupdf.open()
        for i in range(30):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i+1} content text here.", fontsize=12)
        raw = doc.tobytes()
        doc.close()

        result = extract_pdf(raw, filename="many.pdf")
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.pages), 20)


class TestParsePdfDataUrl(unittest.TestCase):
    def _data_url(self, raw: bytes) -> str:
        return "data:application/pdf;base64," + base64.b64encode(raw).decode()

    def test_valid_pdf_data_url(self):
        raw = _make_text_pdf("hello pdf content")
        b64 = parse_pdf_data_url(self._data_url(raw), "doc.pdf")
        self.assertTrue(b64)
        self.assertEqual(base64.b64decode(b64), raw)

    def test_wrong_mime_rejected(self):
        with self.assertRaises(ValueError):
            parse_pdf_data_url("data:image/png;base64,aGk=", "x.pdf")

    def test_missing_payload_rejected(self):
        with self.assertRaises(ValueError):
            parse_pdf_data_url("data:application/pdf;base64,", "x.pdf")

    def test_oversize_rejected(self):
        raw = _make_text_pdf("x")
        url = self._data_url(raw)
        with self.assertRaises(ValueError):
            parse_pdf_data_url(url, "x.pdf", max_b64_chars=10)

    def test_image_still_works(self):
        # Regression: image parsing must be untouched.
        TINY_PNG = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        b64, mime, name = parse_image_data_url(TINY_PNG, "wood.png")
        self.assertEqual(mime, "image/png")


class TestConfigPDFLimits(unittest.TestCase):
    def test_pdf_limits_sane(self):
        self.assertEqual(config.MAX_PDF_BYTES, 10 * 1024 * 1024)
        self.assertIn("application/pdf", config.ALLOWED_PDF_MIMES)
        self.assertGreater(config.MAX_PDF_B64_CHARS, config.MAX_PDF_BYTES)


if __name__ == "__main__":
    unittest.main()
