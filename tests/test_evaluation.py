from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_scoped.bench.evaluation import EvaluationCase, evaluate_retrieval, load_cases
from gdrive_scoped.documents import DocumentService
from gdrive_scoped.drive import FOLDER_MIME_TYPE, DriveItem, SearchPage
from gdrive_scoped.location import DriveKind, DriveLocation
from gdrive_scoped.scope import ScopedDrive


class EvaluationGateway:
    def __init__(self) -> None:
        self.root = DriveItem("root", "Corpus", FOLDER_MIME_TYPE, "drive")
        self.source = DriveItem(
            "source", "Timeline.md", "text/markdown", "drive", parents=("root",)
        )

    async def get_item(self, item_id: str) -> DriveItem:
        return {"root": self.root, "source": self.source}[item_id]

    async def list_folders(self) -> list[DriveItem]:
        return [self.root]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [self.source] if folder_id == "root" else []

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        # The same query without the keyword clause, which is what it is.
        return (await self.search_items(parent_ids, "")).items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        return SearchPage(items=[self.source])

    async def download_item(self, item_id: str) -> bytes:
        return b"timeline"

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        return b"timeline"


def test_evaluation_cases_load_from_a_small_json_file(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        '[{"question":"When?","search_query":"timeline","expected_source":"Timeline.md"}]',
        encoding="utf-8",
    )

    cases = load_cases(path)

    assert cases == [
        EvaluationCase(question="When?", search_query="timeline", expected_source="Timeline.md")
    ]


@pytest.mark.anyio
async def test_evaluation_reports_source_discovery_without_grading_answers() -> None:
    service = DocumentService(
        ScopedDrive(
            EvaluationGateway(),
            DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
            "root",
        )
    )
    cases = [
        EvaluationCase(question="When?", search_query="timeline", expected_source="timeline.md"),
        EvaluationCase(question="Where?", search_query="timeline", expected_source="missing.pdf"),
    ]

    outcomes = await evaluate_retrieval(service, cases)

    assert [outcome.passed for outcome in outcomes] == [True, False]
    assert outcomes[0].returned_sources == ["Timeline.md"]
