"""PDF upload handling for the web UI.

Two kinds of PDFs arrive in the wild:
1. Text PDFs (invoices, quotes, brochures) — extract the text layer.
2. Screenshot PDFs (client conversations saved via print-to-PDF, chat
   exports) — no text layer, just page images. These are rendered to
   PNG and read by the vision model.

Strategy per page: try text extraction first; if the page has little or
no text, render it to an image and hand it to the vision model. This
gives correct behaviour for mixed documents too.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import List, Optional

# --- Limits (kept in one place, unit-testable) --------------------------
MAX_PDF_BYTES = 10 * 1024 * 1024  # matches web MAX_BODY_BYTES
MAX_PDF_PAGES = 20
# A page with fewer extracted characters than this is treated as an
# image-only page (screenshot) and goes to the vision model.
MIN_TEXT_CHARS_PER_PAGE = 30
# Render resolution for vision reading (150 dpi ≈ readable, small payload).
RENDER_DPI = 150
# Cap total vision pages so one upload can't blow the API budget.
MAX_VISION_PAGES = 8


@dataclass
class PDFPage:
    """One page: either extracted text or a rendered image (not both)."""
    page_number: int  # 1-based
    text: str = ""
    image_b64: str = ""  # rendered PNG (base64), for vision reading


@dataclass
class PDFExtraction:
    ok: bool
    filename: str = ""
    pages: List[PDFPage] = field(default_factory=list)
    text_pages: int = 0
    vision_pages: int = 0
    error: str = ""


def _extract_with_pypdf(raw: bytes) -> Optional["pypdf.PdfReader"]:
    try:
        import io

        from pypdf import PdfReader

        return PdfReader(io.BytesIO(raw))
    except Exception:
        return None


def _extract_with_pymupdf(raw: bytes):
    """Return a pymupdf.Document or None."""
    try:
        import pymupdf

        return pymupdf.open(stream=raw, filetype="pdf")
    except Exception:
        return None


def extract_pdf(
    raw: bytes,
    filename: str = "document.pdf",
    max_pages: int = MAX_PDF_PAGES,
    min_text_chars: int = MIN_TEXT_CHARS_PER_PAGE,
    max_vision_pages: int = MAX_VISION_PAGES,
) -> PDFExtraction:
    """Extract a PDF into per-page text and/or rendered images.

    Pure extraction — no LLM calls. Vision reading happens later in
    service.describe_pdf_pages().
    """
    if not raw:
        return PDFExtraction(ok=False, filename=filename, error="empty file")
    if len(raw) > MAX_PDF_BYTES:
        return PDFExtraction(
            ok=False, filename=filename, error=f"PDF too large (max {MAX_PDF_BYTES // (1024*1024)}MB)"
        )

    doc = _extract_with_pymupdf(raw)
    if doc is None:
        return PDFExtraction(
            ok=False, filename=filename, error="could not open PDF (corrupt or not a PDF)"
        )

    pages: List[PDFPage] = []
    text_pages = 0
    vision_pages = 0
    try:
        total = min(doc.page_count, max_pages)
        for i in range(total):
            page = doc.load_page(i)
            text = (page.get_text() or "").strip()
            if len(text) >= min_text_chars:
                pages.append(PDFPage(page_number=i + 1, text=text))
                text_pages += 1
            else:
                # Image-only page (screenshot): render to PNG for vision.
                if vision_pages < max_vision_pages:
                    pix = page.get_pixmap(dpi=RENDER_DPI)
                    image_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
                    pages.append(PDFPage(page_number=i + 1, image_b64=image_b64))
                    vision_pages += 1
                else:
                    pages.append(
                        PDFPage(
                            page_number=i + 1,
                            text=f"[page {i + 1}: image-only, skipped — vision page limit reached]",
                        )
                    )
                    text_pages += 1
    finally:
        doc.close()

    if not pages:
        return PDFExtraction(ok=False, filename=filename, error="PDF has no readable pages")

    return PDFExtraction(
        ok=True,
        filename=filename,
        pages=pages,
        text_pages=text_pages,
        vision_pages=vision_pages,
    )


def build_pdf_note(extraction: PDFExtraction) -> str:
    """Build the [PDF attached ...] note injected into the user message.

    Text pages are inlined; vision pages are placeholders filled in
    later by service.describe_pdf_pages().
    """
    lines = [
        f"[PDF attached: {extraction.filename}, {len(extraction.pages)} page(s): "
        f"{extraction.text_pages} text, {extraction.vision_pages} image-only]"
    ]
    for p in extraction.pages:
        if p.text:
            lines.append(f"--- page {p.page_number} (text) ---\n{p.text}")
        else:
            lines.append(f"--- page {p.page_number} (image, vision reading pending) ---")
    return "\n".join(lines)
