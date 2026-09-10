"""Page-aware PDF text extraction."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from gdrive_scoped.extractors.models import ExtractedDocument, TextBlock


def extract_pdf(content: bytes) -> ExtractedDocument:
    reader = PdfReader(BytesIO(content))
    return ExtractedDocument(
        tuple(
            TextBlock(location=f"page {page_number}", text=page.extract_text() or "")
            for page_number, page in enumerate(reader.pages, start=1)
        )
    )
