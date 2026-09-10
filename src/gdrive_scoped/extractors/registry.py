"""MIME-directed extraction without leaking format choices into tool handlers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gdrive_scoped.errors import UnsupportedContentType
from gdrive_scoped.extractors.markup import extract_html
from gdrive_scoped.extractors.models import ExtractedDocument, TextBlock
from gdrive_scoped.extractors.pdf import extract_pdf
from gdrive_scoped.extractors.slides import extract_presentation
from gdrive_scoped.extractors.spreadsheet import extract_workbook
from gdrive_scoped.extractors.word import extract_word_document

GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
MARKDOWN_MIME_TYPE = "text/markdown"
PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    export_mime_type: str | None
    extractor: Callable[[bytes], ExtractedDocument]


class ExtractorRegistry:
    """Select an export format and parser from a Drive MIME type."""

    def __init__(self) -> None:
        text_plan = ExtractionPlan(export_mime_type=None, extractor=_extract_text)
        # Any `text/*` type not claimed below falls back to this: a corpus holds
        # whatever people uploaded, and a text type nobody listed is legible as it
        # stands. Refusing it would send a caller looking for a parser that would
        # only have decoded bytes.
        self._text_plan = text_plan
        self._plans: dict[str, ExtractionPlan] = {
            "text/plain": text_plan,
            "text/markdown": text_plan,
            "text/csv": text_plan,
            "application/json": text_plan,
            # Added from a census of a real corpus, which found exactly these
            # among its unreadable share. Their markup is the content, so they
            # pass through as they stand.
            "text/tab-separated-values": text_plan,
            "text/xml": text_plan,
            "application/xml": text_plan,
            # Text formats that do not announce themselves under `text/`.
            "application/x-yaml": text_plan,
            "application/yaml": text_plan,
            "application/x-ndjson": text_plan,
            # HTML does not: a page is mostly tags, and passing that through
            # would spend a chunk on angle brackets rather than on the text.
            "text/html": ExtractionPlan(export_mime_type=None, extractor=extract_html),
            GOOGLE_DOC_MIME_TYPE: ExtractionPlan(
                export_mime_type=MARKDOWN_MIME_TYPE,
                extractor=_extract_text,
            ),
            GOOGLE_SHEET_MIME_TYPE: ExtractionPlan(
                export_mime_type=XLSX_MIME_TYPE,
                extractor=extract_workbook,
            ),
            XLSX_MIME_TYPE: ExtractionPlan(
                export_mime_type=None,
                extractor=extract_workbook,
            ),
            GOOGLE_SLIDES_MIME_TYPE: ExtractionPlan(
                export_mime_type=PPTX_MIME_TYPE,
                extractor=extract_presentation,
            ),
            PPTX_MIME_TYPE: ExtractionPlan(
                export_mime_type=None,
                extractor=extract_presentation,
            ),
            DOCX_MIME_TYPE: ExtractionPlan(
                export_mime_type=None,
                extractor=extract_word_document,
            ),
            PDF_MIME_TYPE: ExtractionPlan(
                export_mime_type=None,
                extractor=extract_pdf,
            ),
        }

    def supports(self, mime_type: str) -> bool:
        """Whether any extractor claims this type. Asking is not a failure."""

        return mime_type in self._plans or mime_type.startswith("text/")

    def plan_for(self, mime_type: str) -> ExtractionPlan:
        plan = self._plans.get(mime_type)
        if plan is None and mime_type.startswith("text/"):
            plan = self._text_plan
        if plan is None:
            raise UnsupportedContentType(f"Unsupported content type: {mime_type}")
        return plan


def _extract_text(content: bytes) -> ExtractedDocument:
    text = content.decode("utf-8-sig", errors="replace")
    return ExtractedDocument((TextBlock(location="text", text=text),))
