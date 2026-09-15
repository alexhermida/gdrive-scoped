"""Small deterministic harness for the Drive-native retrieval hypothesis."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from gdrive_scoped.census import PARENT_BATCH_SIZE
from gdrive_scoped.documents import DocumentService
from gdrive_scoped.errors import DriveError
from gdrive_scoped.extractors import ExtractorRegistry
from gdrive_scoped.scope import ScopedDrive

#: An evaluation file holds a handful of cases, not a test suite: every case
#: costs three round trips per iteration, and a benchmark nobody waits for is
#: a benchmark nobody runs.
MAX_CASES = 10

#: How many to derive when nobody says. Enough to cover the format spread of a
#: real corpus, few enough that the run stays short.
DEFAULT_CASE_COUNT = 6

#: How deep the verification search looks, and the `limit` each derived case
#: is then measured with. The two are the same number on purpose: a case
#: verified at ten and measured at three fails on a document that is findable.
VERIFY_LIMIT = 10


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1)
    search_query: str = Field(min_length=1)
    expected_source: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class EvaluationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    search_query: str
    expected_source: str
    returned_sources: list[str]
    passed: bool


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = TypeAdapter(list[EvaluationCase]).validate_json(path.read_text(encoding="utf-8"))
    if not 1 <= len(cases) <= MAX_CASES:
        raise ValueError(f"Evaluation file must contain between 1 and {MAX_CASES} cases")
    return cases


def save_cases(cases: list[EvaluationCase], path: Path) -> None:
    """Write cases where `load_cases` can read them back."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([case.model_dump() for case in cases], indent=2) + "\n", encoding="utf-8"
    )


async def derive_cases(
    scoped_drive: ScopedDrive,
    wanted: int = DEFAULT_CASE_COUNT,
    extractors: ExtractorRegistry | None = None,
) -> list[EvaluationCase]:
    """The largest document of each MIME type that is found by a search and reads.

    Derived rather than hand-written so a first run against an unfamiliar
    corpus measures *that* corpus, instead of failing on paths that only ever
    existed in somebody else's. Largest of each type because size and format
    are what a read's latency is actually made of.

    The verification is the half that matters. A case whose search returns
    nothing measures a search that found nothing and a read that never
    happened, which reads as a fast benchmark rather than a broken one. And an
    extractor claiming the MIME type is not a read: a scan with no text or a
    sheet over the export cap would be chosen for its size and abort the
    benchmark at its first read, so each case is read once before it is kept.
    """

    if not 1 <= wanted <= MAX_CASES:
        raise ValueError(f"Derived case count must be between 1 and {MAX_CASES}")

    registry = extractors or ExtractorRegistry()
    service = DocumentService(scoped_drive, extractors=registry)
    folder_paths = await scoped_drive.folder_paths()
    folder_ids = tuple(folder_paths)

    candidates_by_type: dict[str, list[_Candidate]] = {}
    for offset in range(0, len(folder_ids), PARENT_BATCH_SIZE):
        batch = folder_ids[offset : offset + PARENT_BATCH_SIZE]
        for item in await scoped_drive.gateway.list_descendants(batch):
            if not registry.supports(item.mime_type):
                continue
            folder_path = folder_paths.get(item.parents[0] if item.parents else "")
            if folder_path is None:
                continue
            relative_path = item.name if folder_path == "." else f"{folder_path}/{item.name}"
            candidates_by_type.setdefault(item.mime_type, []).append(
                _Candidate(item.size or 0, item.name, relative_path, item.id)
            )

    cases: list[EvaluationCase] = []
    # Types in order of their largest document; within a type, largest first, and
    # the first one that is found and reads is the case for that type.
    for candidates in sorted(candidates_by_type.values(), key=max, reverse=True):
        if len(cases) == wanted:
            break
        for candidate in sorted(candidates, reverse=True):
            case = await _verified_case(scoped_drive, service, candidate)
            if case is not None:
                cases.append(case)
                break
    return cases


class _Candidate(NamedTuple):
    size: int
    name: str
    relative_path: str
    item_id: str


async def _verified_case(
    scoped_drive: ScopedDrive, service: DocumentService, candidate: _Candidate
) -> EvaluationCase | None:
    keyword = _keyword(candidate.name)
    if keyword is None:
        return None
    found = await scoped_drive.search(keyword, limit=VERIFY_LIMIT)
    if not any(item.relative_path == candidate.relative_path for item in found.items):
        return None
    # The read is the expensive check, so it comes last. One character is enough
    # to force the download and the parse, which is all that can fail.
    try:
        await service.read_document(candidate.item_id, max_chars=1)
    except DriveError:
        return None
    return EvaluationCase(
        question=f"What does {candidate.name} say?",
        search_query=keyword,
        expected_source=candidate.relative_path,
        limit=VERIFY_LIMIT,
    )


def _keyword(name: str) -> str | None:
    """The longest word of a file's own name, as the query to find it by.

    A word taken from the corpus rather than guessed at, which makes the case
    a fact about Drive's index instead of a hope about its vocabulary. Short
    words are skipped because Drive's own tokenizer does little with them.
    """

    words = [word for word in re.split(r"[^\w]+", Path(name).stem) if len(word) >= 4]
    return max(words, key=len) if words else None


async def evaluate_retrieval(
    service: DocumentService, cases: list[EvaluationCase]
) -> list[EvaluationOutcome]:
    outcomes: list[EvaluationOutcome] = []
    for case in cases:
        results = await service.search_documents(case.search_query, limit=case.limit)
        sources = [result.relative_path for result in results.items]
        expected = case.expected_source.casefold()
        outcomes.append(
            EvaluationOutcome(
                question=case.question,
                search_query=case.search_query,
                expected_source=case.expected_source,
                returned_sources=sources,
                passed=any(expected in source.casefold() for source in sources),
            )
        )
    return outcomes
