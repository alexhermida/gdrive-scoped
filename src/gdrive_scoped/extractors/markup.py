"""HTML to readable text, using only the standard library.

XML and TSV pass through as text — they are legible as they stand, and their
markup *is* the content. HTML is not: a page is mostly tags, and handing that
to a model spends most of a chunk on angle brackets and CSS. Twenty lines of
`html.parser` buys the difference, with no dependency to justify.
"""

from __future__ import annotations

from html.parser import HTMLParser

from gdrive_scoped.extractors.models import ExtractedDocument, TextBlock

#: Elements whose content is markup for the browser, not text for a reader.
_SILENT = frozenset({"script", "style", "head", "meta", "link", "noscript"})

#: Elements after which a line break is part of the meaning.
_BREAKS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "table",
    }
)


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._silent_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SILENT:
            self._silent_depth += 1
        elif tag in _BREAKS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SILENT:
            self._silent_depth = max(0, self._silent_depth - 1)
        elif tag in _BREAKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._silent_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        # Collapse the runs of blank lines the tag boundaries leave behind,
        # without touching indentation inside a line.
        lines = [line.strip() for line in "".join(self._parts).splitlines()]
        kept: list[str] = []
        for line in lines:
            if line or (kept and kept[-1]):
                kept.append(line)
        return "\n".join(kept).strip()


def extract_html(content: bytes) -> ExtractedDocument:
    collector = _TextCollector()
    collector.feed(content.decode("utf-8-sig", errors="replace"))
    collector.close()
    return ExtractedDocument((TextBlock(location="text", text=collector.text()),))
