"""Small deterministic harness for the Drive-native retrieval hypothesis."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from gdrive_scoped.documents import DocumentService


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
    if not 1 <= len(cases) <= 10:
        raise ValueError("Evaluation file must contain between 1 and 10 cases")
    return cases


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
