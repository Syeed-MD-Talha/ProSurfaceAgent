"""Bounded extraction of supported text and Office attachments."""

import io
import unittest
import zipfile

from prosurface_agent.documents import extract_document


def _office_zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class TestTextDocuments(unittest.TestCase):
    def test_txt_utf8(self):
        result = extract_document("Marble quote: $450".encode(), "quote.txt")
        self.assertEqual(result, "Marble quote: $450")

    def test_csv_rows_are_readable(self):
        result = extract_document(b"Item,Price\nPolishing,450\n", "quote.csv")
        self.assertEqual(result, "Item | Price\nPolishing | 450")

    def test_empty_text_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            extract_document(b"", "empty.txt")


class TestOfficeDocuments(unittest.TestCase):
    def test_docx_paragraphs_extracted(self):
        raw = _office_zip(
            {
                "word/document.xml": (
                    "<document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
                    "<body><p><r><t>Floor repair quote</t></r></p>"
                    "<p><r><t>Total: $450</t></r></p></body></document>"
                )
            }
        )
        result = extract_document(raw, "quote.docx")
        self.assertEqual(result, "Floor repair quote\nTotal: $450")

    def test_xlsx_shared_and_inline_strings_extracted(self):
        raw = _office_zip(
            {
                "xl/sharedStrings.xml": (
                    "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                    "<si><t>Service</t></si><si><t>Marble polishing</t></si></sst>"
                ),
                "xl/worksheets/sheet1.xml": (
                    "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                    "<sheetData><row><c t='s'><v>0</v></c><c><v>450</v></c></row>"
                    "<row><c t='s'><v>1</v></c><c t='inlineStr'><is><t>Singapore</t></is></c>"
                    "</row></sheetData></worksheet>"
                ),
            }
        )
        result = extract_document(raw, "quote.xlsx")
        self.assertEqual(result, "Sheet 1:\nService | 450\nMarble polishing | Singapore")

    def test_invalid_office_archive_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid Office document"):
            extract_document(b"not a zip", "quote.docx")

    def test_legacy_office_formats_rejected(self):
        with self.assertRaisesRegex(ValueError, "supported document formats"):
            extract_document(b"data", "old-quote.doc")


if __name__ == "__main__":
    unittest.main()
