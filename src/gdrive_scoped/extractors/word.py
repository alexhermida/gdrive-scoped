"""DOCX extraction with section and table locations."""

from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from gdrive_scoped.extractors.models import ExtractedDocument, TextBlock


def extract_word_document(content: bytes) -> ExtractedDocument:
    document = Document(BytesIO(content))
    blocks: list[TextBlock] = []
    location = "document"
    lines: list[str] = []
    table_number = 0

    for element in document.iter_inner_content():
        if isinstance(element, Table):
            if lines:
                blocks.append(TextBlock(location=location, text="\n".join(lines)))
                lines = []
            table_number += 1
            rows = ["\t".join(cell.text.strip() for cell in row.cells) for row in element.rows]
            blocks.append(TextBlock(location=f"table {table_number}", text="\n".join(rows)))
            continue

        paragraph = element
        if not isinstance(paragraph, Paragraph):
            continue
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if style_name.startswith("Heading"):
            if lines:
                blocks.append(TextBlock(location=location, text="\n".join(lines)))
            location = f"section: {text}"
            lines = [text]
        else:
            lines.append(text)
    if lines:
        blocks.append(TextBlock(location=location, text="\n".join(lines)))

    return ExtractedDocument(tuple(blocks))
