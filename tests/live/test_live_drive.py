from __future__ import annotations

import os

import pytest

from gdrive_scoped.documents import DocumentService
from gdrive_scoped.drive import create_gateway
from gdrive_scoped.env import ConfigurationError, Settings, credentials_from_environ
from gdrive_scoped.scope import ScopedDrive

LIVE_SEARCH_QUERY_ENV = "GDRIVE_LIVE_SEARCH_QUERY"
LIVE_EXPECTED_SOURCE_ENV = "GDRIVE_LIVE_EXPECTED_SOURCE"


def _live_drive_is_configured() -> bool:
    try:
        Settings.from_environ()
    except ConfigurationError:
        return False
    return True


LIVE_CONFIGURED = _live_drive_is_configured()
LIVE_READ_CONFIGURED = LIVE_CONFIGURED and bool(
    os.getenv(LIVE_SEARCH_QUERY_ENV) and os.getenv(LIVE_EXPECTED_SOURCE_ENV)
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_CONFIGURED, reason="live Drive environment is not configured"),
]


@pytest.mark.anyio
async def test_configured_root_can_be_validated_and_listed_read_only() -> None:
    settings = Settings.from_environ()
    scoped_drive = ScopedDrive(
        gateway=create_gateway(credentials_from_environ(), settings.location),
        location=settings.location,
        root_folder_id=settings.root_folder_id,
    )

    root = await scoped_drive.initialize()
    children = await scoped_drive.list_folder()

    assert root.id == settings.root_folder_id
    # Every listed child is addressable by its own Drive ID, which is what the
    # tools hand back and what a caller may paste from a Drive link.
    assert all(child.id for child in children)


@pytest.mark.skipif(
    not LIVE_READ_CONFIGURED,
    reason="live search query and expected source are not configured",
)
@pytest.mark.anyio
async def test_configured_root_can_be_searched_and_read_read_only() -> None:
    settings = Settings.from_environ()
    scoped_drive = ScopedDrive(
        gateway=create_gateway(credentials_from_environ(), settings.location),
        location=settings.location,
        root_folder_id=settings.root_folder_id,
    )
    await scoped_drive.initialize()
    service = DocumentService(scoped_drive)
    query = os.environ[LIVE_SEARCH_QUERY_ENV]
    expected_source = os.environ[LIVE_EXPECTED_SOURCE_ENV]

    results = await service.search_documents(query, limit=50)
    matching = [item for item in results.items if item.relative_path == expected_source]

    found = [item.relative_path for item in results.items]
    assert matching, f"Expected {expected_source!r} in {found}"
    metadata = await service.get_metadata(matching[0].id)
    content = await service.read_document(matching[0].id, max_chars=1_000)
    assert metadata.relative_path == expected_source
    assert content.content
