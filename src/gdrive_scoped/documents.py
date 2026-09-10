"""Transport-independent document retrieval use cases."""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass, field

from gdrive_scoped.errors import EmptyDocument, ExportTooLarge, InvalidCursor
from gdrive_scoped.extractors import ExtractedDocument, ExtractorRegistry
from gdrive_scoped.extractors.registry import ExtractionPlan
from gdrive_scoped.scope import (
    AuthorizedItem,
    ScopedDrive,
    ScopedItem,
    SearchResult,
    audit,
    audit_caller,
)

#: The largest chunk a caller may ask for. Lowered from 50,000: the Agent
#: Engine SSE stream corrupts at roughly 100 KB per tool result, and 40,000
#: characters of non-Latin text is already most of that budget once encoded.
MAX_READ_CHARS = 40_000
DEFAULT_READ_CHARS = 25_000

#: The hard ceiling, applied to the *encoded* chunk after the character cut.
#: `max_chars` counts characters; a transport counts bytes, and for this
#: domain the two differ a lot — CJK text is three bytes per character, and
#: even α-tocopherol and µg cost two.
MAX_READ_BYTES = 64_000

#: Parsed documents held per process. The dict this replaces never evicted:
#: tolerable in a stdio process that exits with the session, unbounded growth
#: in a server instance that lives for days.
DEFAULT_CACHE_SIZE = 32

#: Refuse to download a blob larger than this. Drive's own 10 MB cap applies
#: to Workspace *exports*; an uploaded file can be any size at all.
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class ReadResult:
    """One bounded chunk of an authorized document.

    Frozen but not `slots=True`, unlike the rest of the core: an adapter
    publishing this as structured output derives a schema from it, and a
    slotted dataclass has none.
    """

    id: str
    name: str
    mime_type: str
    relative_path: str
    content: str
    location: str
    next_cursor: str | None
    #: Length of the whole extracted document, so a caller can see what
    #: fraction it was handed.
    total_chars: int = 0
    #: Present only on a partial read. A model that is not told it saw part of
    #: a document answers as though it saw all of it, and nothing in the
    #: content itself reveals the difference.
    note: str | None = None


@dataclass(frozen=True)
class FolderPage:
    """One page of an authorized folder listing. See `ReadResult` on `slots`."""

    items: list[ScopedItem]
    next_cursor: str | None


@dataclass(slots=True)
class DocumentService:
    """Small public interface over folder-scoped Drive retrieval."""

    scoped_drive: ScopedDrive
    extractors: ExtractorRegistry = field(default_factory=ExtractorRegistry)
    cache_size: int = DEFAULT_CACHE_SIZE
    max_read_chars: int = MAX_READ_CHARS
    max_read_bytes: int = MAX_READ_BYTES
    max_download_bytes: int = MAX_DOWNLOAD_BYTES
    _cache: OrderedDict[tuple[str, str | None, str], ExtractedDocument] = field(
        default_factory=OrderedDict, init=False, repr=False
    )

    async def search_documents(self, query: str, limit: int = 10) -> SearchResult:
        """Search the Authorized Subtree with focused Drive keywords."""

        keywords = query.strip()
        if not keywords:
            raise ValueError("Search query must not be empty")
        if not 1 <= limit <= 50:
            raise ValueError("Search limit must be between 1 and 50")
        result = await self.scoped_drive.search(keywords, limit=limit)
        _record(
            "search",
            query=keywords,
            limit=limit,
            returned=len(result.items),
            truncated=result.truncated,
            incomplete=result.incomplete,
            file_ids=[item.id for item in result.items],
        )
        return result

    async def list_folder(
        self,
        folder_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> FolderPage:
        if not 1 <= limit <= 200:
            raise ValueError("Folder listing limit must be between 1 and 200")
        items = await self.scoped_drive.list_folder(folder_id)
        start = _decode_cursor(cursor)
        if start > len(items):
            raise InvalidCursor("Folder cursor is outside the current listing")
        end = min(start + limit, len(items))
        next_cursor = _encode_cursor(end) if end < len(items) else None
        page = FolderPage(items=items[start:end], next_cursor=next_cursor)
        _record(
            "list_folder",
            folder_id=folder_id or self.scoped_drive.root_folder_id,
            returned=len(page.items),
            file_ids=[item.id for item in page.items],
        )
        return page

    async def get_metadata(self, file_id: str) -> ScopedItem:
        item = await self.scoped_drive.get_metadata(file_id)
        _record("get_metadata", file_id=file_id)
        return item

    async def read_document(
        self,
        file_id: str,
        cursor: str | None = None,
        max_chars: int = DEFAULT_READ_CHARS,
    ) -> ReadResult:
        """Read one bounded normalized chunk after current scope validation."""

        if not 1 <= max_chars <= self.max_read_chars:
            raise ValueError(f"max_chars must be between 1 and {self.max_read_chars}")

        # One authorization, whether or not the parsed document is already cached: the
        # proof is what gates the read, and it is made against Drive on every call.
        authorized = await self.scoped_drive.authorize_document(file_id)
        item = authorized.item
        plan = self.extractors.plan_for(item.mime_type)
        extracted = await self._extracted(authorized, plan)
        if not extracted.text.strip():
            raise EmptyDocument(_empty_document_message(item.mime_type))

        start = _decode_cursor(cursor)
        chunk = _read_chunk(extracted, start, max_chars, self.max_read_bytes)
        _record(
            "read_document",
            file_id=file_id,
            start=start,
            chars=len(chunk.content),
            partial=start > 0 or chunk.next_cursor is not None,
        )
        return ReadResult(
            id=file_id,
            name=item.name,
            mime_type=item.mime_type,
            relative_path=authorized.relative_path,
            content=chunk.content,
            location=chunk.location,
            next_cursor=chunk.next_cursor,
            total_chars=len(extracted.text),
            note=chunk.note,
        )

    async def _extracted(
        self, authorized: AuthorizedItem, plan: ExtractionPlan
    ) -> ExtractedDocument:
        """The parsed document, from the LRU if this exact version is in it.

        Keyed on the modification time as well as the ID, so an edited
        document is re-fetched rather than served stale. The cache holds bytes
        only — never a decision — which is what lets one read still perform
        exactly one ancestry proof.
        """

        item = authorized.item
        cache_key = (item.id, item.modified_time, item.mime_type)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        # The guard is only meaningful for a direct download: a Workspace file
        # reports no `size`, and its export is bounded by Drive's own 10 MB cap.
        oversized = (
            plan.export_mime_type is None
            and item.size is not None
            and item.size > self.max_download_bytes
        )
        if oversized:
            raise ExportTooLarge(
                f"{item.name} is {(item.size or 0) / 1_048_576:.1f} MB, over the "
                f"{self.max_download_bytes // 1_048_576} MB read limit"
            )

        content_bytes = await self.scoped_drive.download(authorized, plan.export_mime_type)
        extracted = await asyncio.to_thread(plan.extractor, content_bytes)
        self._cache[cache_key] = extracted
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return extracted


def _record(event: str, **fields: object) -> None:
    """One audit record per completed operation.

    Scoped Drive records what the boundary decided; this records what the
    caller actually did with it — which documents a search surfaced, which
    chunk of which file was read — so "what did the agent read, and for whom"
    is answerable from the log alone. Refusals are already recorded where they
    happen and are not repeated here.
    """

    audit.info("Served %s", event, extra={"event": event, "caller": audit_caller.get(), **fields})


@dataclass(frozen=True, slots=True)
class _Chunk:
    content: str
    location: str
    next_cursor: str | None
    note: str | None


def _read_chunk(document: ExtractedDocument, start: int, max_chars: int, max_bytes: int) -> _Chunk:
    text = document.text
    if start < 0 or start > len(text):
        raise InvalidCursor("Read cursor is outside the document")

    end = _cut(document, text, start, min(start + max_chars, len(text)))
    while end > start + 1 and len(text[start:end].encode("utf-8")) > max_bytes:
        # Encoded size is not a function of the character count, so this cannot
        # be solved arithmetically for arbitrary text. Halving the overshoot
        # converges in a few passes and still prefers a block boundary.
        overshoot = len(text[start:end].encode("utf-8")) - max_bytes
        end = _cut(document, text, start, max(start + 1, end - max(1, overshoot // 4)))

    next_cursor = _encode_cursor(end) if end < len(text) else None
    return _Chunk(
        content=text[start:end],
        location=", ".join(_locations_for_range(document, start, end)),
        next_cursor=next_cursor,
        note=_partial_read_note(document, start, end, len(text)) if next_cursor else None,
    )


def _cut(document: ExtractedDocument, text: str, start: int, end: int) -> int:
    """Move `end` back to the last block boundary inside the window, if any."""

    boundaries = [boundary for boundary in _block_boundaries(document) if start < boundary <= end]
    return boundaries[-1] if boundaries else end


def _partial_read_note(document: ExtractedDocument, start: int, end: int, total: int) -> str:
    """Say plainly that this is part of a document, and how to get the rest."""

    where = _locations_for_range(document, start, end)
    everywhere = [block.location for block in document.blocks]
    span = f"Characters {start:,}-{end:,} of {total:,}"
    if where and len(everywhere) > 1:
        span += f" ({where[0]} to {where[-1]}, of {len(everywhere)} sections)"
    return f"{span}. This is part of the document; pass next_cursor to continue."


def _empty_document_message(mime_type: str) -> str:
    """Distinguish "nothing to extract" from "cannot extract".

    A scanned PDF parses perfectly and yields whitespace. Reporting that as an
    unsupported type sends the caller looking for a parser that already ran.
    """

    if mime_type == "application/pdf":
        return (
            "This PDF contains no extractable text - it is most likely a scan of "
            "images, and there is no OCR."
        )
    return "This document contains no extractable text."


def _locations_for_range(document: ExtractedDocument, start: int, end: int) -> list[str]:
    locations: list[str] = []
    offset = 0
    for block in document.blocks:
        block_end = offset + len(block.text)
        if start < block_end and end > offset:
            locations.append(block.location)
        offset = block_end + 2
    return locations


def _block_boundaries(document: ExtractedDocument) -> list[int]:
    boundaries: list[int] = []
    offset = 0
    for block in document.blocks[:-1]:
        offset += len(block.text) + 2
        boundaries.append(offset)
    return boundaries


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
        return int(value)
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidCursor("Invalid read cursor") from error
