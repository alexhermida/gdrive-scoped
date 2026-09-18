from __future__ import annotations

import json
import logging
import threading
from typing import Any

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.errors import HttpError

from gdrive_scoped import drive
from gdrive_scoped.drive import DriveItem, GoogleDriveGateway, create_gateway
from gdrive_scoped.errors import (
    CredentialsRejected,
    DriveNotFound,
    DriveUnavailable,
    EnumerationBudgetExceeded,
    ExportTooLarge,
)


class StaticRequest:
    def __init__(self, response: Any, calls: list[dict[str, Any]]) -> None:
        self._response = response
        self._calls = calls

    def execute(self, **kwargs: Any) -> Any:
        self._calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class RecordingFiles:
    def __init__(
        self,
        response: dict[str, Any] | Exception,
        list_responses: dict[str | None, dict[str, Any] | Exception] | None = None,
    ) -> None:
        self.response = response
        self.list_responses = list_responses or {}
        self.execute_calls: list[dict[str, Any]] = []
        self.get_arguments: dict[str, Any] | None = None
        self.list_arguments: list[dict[str, Any]] = []
        self.get_media_arguments: dict[str, Any] | None = None
        self.export_media_arguments: dict[str, Any] | None = None

    def get(self, **kwargs: Any) -> StaticRequest:
        self.get_arguments = kwargs
        return StaticRequest(self.response, self.execute_calls)

    def list(self, **kwargs: Any) -> StaticRequest:
        self.list_arguments.append(kwargs)
        return StaticRequest(self.list_responses[kwargs.get("pageToken")], self.execute_calls)

    def get_media(self, **kwargs: Any) -> StaticRequest:
        self.get_media_arguments = kwargs
        return StaticRequest(self._transfer_result(b"downloaded"), self.execute_calls)

    def export_media(self, **kwargs: Any) -> StaticRequest:
        self.export_media_arguments = kwargs
        return StaticRequest(self._transfer_result(b"exported"), self.execute_calls)

    def _transfer_result(self, content: bytes) -> Any:
        return self.response if isinstance(self.response, Exception) else content


class RecordingAbout:
    def __init__(self, response: dict[str, Any], calls: list[dict[str, Any]]) -> None:
        self._response = response
        self._calls = calls
        self.get_arguments: dict[str, Any] | None = None

    def get(self, **kwargs: Any) -> StaticRequest:
        self.get_arguments = kwargs
        return StaticRequest(self._response, self._calls)


class RecordingService:
    def __init__(self, files: RecordingFiles, about: RecordingAbout | None = None) -> None:
        self._files = files
        self._about = about or RecordingAbout({"user": {"emailAddress": "nobody@example.org"}}, [])

    def files(self) -> RecordingFiles:
        return self._files

    def about(self) -> RecordingAbout:
        return self._about


def make_gateway(files: RecordingFiles) -> GoogleDriveGateway:
    return GoogleDriveGateway(RecordingService(files), credentials=UserCredentials(None))


@pytest.mark.anyio
async def test_gateway_gets_shared_drive_metadata_with_narrow_fields() -> None:
    files = RecordingFiles(
        {
            "id": "file-1",
            "name": "Report",
            "mimeType": "text/plain",
            "driveId": "drive-1",
            "parents": ["root-1"],
            "capabilities": {"canDownload": True},
        }
    )
    gateway = make_gateway(files)

    item = await gateway.get_item("file-1")

    assert item == DriveItem(
        id="file-1",
        name="Report",
        mime_type="text/plain",
        drive_id="drive-1",
        parents=("root-1",),
    )
    assert files.get_arguments is not None
    assert files.get_arguments["fileId"] == "file-1"
    assert files.get_arguments["supportsAllDrives"] is True
    assert "shortcutDetails" not in files.get_arguments["fields"]


@pytest.mark.anyio
async def test_gateway_fails_closed_when_download_capability_is_absent() -> None:
    files = RecordingFiles(
        {
            "id": "file-1",
            "name": "Restricted",
            "mimeType": "application/pdf",
            "driveId": "drive-1",
            "parents": ["root-1"],
        }
    )

    item = await make_gateway(files).get_item("file-1")

    assert item.can_download is False


@pytest.mark.anyio
async def test_gateway_lists_every_shared_drive_page_and_deduplicates_items() -> None:
    report = {
        "id": "file-1",
        "name": "Report",
        "mimeType": "text/plain",
        "driveId": "drive-1",
        "parents": ["folder'1"],
    }
    appendix = {
        "id": "file-2",
        "name": "Appendix",
        "mimeType": "application/pdf",
        "driveId": "drive-1",
        "parents": ["folder'1"],
    }
    files = RecordingFiles(
        {},
        list_responses={
            None: {"files": [report], "nextPageToken": "page-2"},
            "page-2": {"files": [report, appendix]},
        },
    )
    gateway = make_gateway(files)

    items = await gateway.list_children("folder'1")

    assert [item.id for item in items] == ["file-1", "file-2"]
    assert len(files.list_arguments) == 2
    first_request = files.list_arguments[0]
    assert first_request["corpora"] == "user"
    assert "driveId" not in first_request
    assert first_request["includeItemsFromAllDrives"] is True
    assert first_request["supportsAllDrives"] is True
    assert first_request["orderBy"] == "name_natural"
    assert "'folder\\'1' in parents" in first_request["q"]
    # A listing carries no `fullText` term, so it may be sorted.
    assert first_request["orderBy"] == "name_natural"


@pytest.mark.anyio
async def test_gateway_sends_one_request_shape_wherever_the_corpus_lives() -> None:
    """`includeItemsFromAllDrives` is unconditional.

    The gateway is not told where the corpus is any more — `ScopedDrive`
    measures that from the root folder — so it asks for everything the identity
    can see and lets the boundary above it decide. A My Drive corpus therefore
    sees Shared Drive rows in the raw answer, and `DriveLocation.contains`
    drops every one of them.
    """

    report = {
        "id": "file-1",
        "name": "Report",
        "mimeType": "text/plain",
        "parents": ["folder-1"],
    }
    files = RecordingFiles({}, list_responses={None: {"files": [report]}})
    gateway = make_gateway(files)

    items = await gateway.list_children("folder-1")

    assert items == [
        DriveItem(
            id="file-1",
            name="Report",
            mime_type="text/plain",
            drive_id=None,
            parents=("folder-1",),
            can_download=False,
        )
    ]
    [request] = files.list_arguments
    assert request["corpora"] == "user"
    assert request["includeItemsFromAllDrives"] is True
    assert "driveId" not in request


@pytest.mark.anyio
async def test_gateway_searches_my_drive_with_server_generated_parent_constraints() -> None:
    report = {
        "id": "file-1",
        "name": "Budget",
        "mimeType": "text/plain",
        "parents": ["nested"],
    }
    files = RecordingFiles({}, list_responses={None: {"files": [report]}})
    gateway = make_gateway(files)

    page = await gateway.search_items(("root", "nested"), "quarterly plan", limit=10)

    assert [item.id for item in page.items] == ["file-1"]
    assert page.incomplete is False
    assert page.has_more is False
    [request] = files.list_arguments
    assert request["corpora"] == "user"
    assert "driveId" not in request
    assert "orderBy" not in request
    assert "('root' in parents or 'nested' in parents)" in request["q"]
    assert "fullText contains 'quarterly plan'" in request["q"]


def test_the_gateway_is_built_over_the_credentials_it_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials are an argument, never discovered here.

    This is the seam that lets one core serve a local process on ADC and a
    deployment on a bot user's refresh token.
    """
    credentials = UserCredentials(None)
    build_arguments: dict[str, Any] = {}

    def fake_build(api: str, version: str, **kwargs: Any) -> RecordingService:
        build_arguments.update(api=api, version=version, **kwargs)
        return RecordingService(RecordingFiles({}))

    monkeypatch.setattr(drive, "build", fake_build)

    gateway = create_gateway(credentials)

    assert isinstance(gateway, GoogleDriveGateway)
    assert build_arguments == {
        "api": "drive",
        "version": "v3",
        "credentials": credentials,
        "cache_discovery": False,
    }


@pytest.mark.anyio
async def test_gateway_builds_search_queries_instead_of_accepting_drive_syntax() -> None:
    report = {
        "id": "file-1",
        "name": "Budget",
        "mimeType": "text/plain",
        "driveId": "drive-1",
        "parents": ["nested"],
    }
    files = RecordingFiles({}, list_responses={None: {"files": [report]}})
    gateway = make_gateway(files)

    page = await gateway.search_items(("root", "nested"), "budget's \\ plan", limit=10)

    assert [item.id for item in page.items] == ["file-1"]
    [request] = files.list_arguments
    assert "('root' in parents or 'nested' in parents)" in request["q"]
    assert "fullText contains 'budget\\'s \\\\ plan'" in request["q"]
    assert "mimeType != 'application/vnd.google-apps.folder'" in request["q"]
    assert request["corpora"] == "user"
    assert "driveId" not in request
    assert request["includeItemsFromAllDrives"] is True
    # Drive refuses `fullText` combined with `orderBy`; sending one fails the whole search.
    assert "orderBy" not in request


@pytest.mark.anyio
async def test_gateway_downloads_and_exports_with_read_only_methods() -> None:
    files = RecordingFiles({})
    gateway = make_gateway(files)

    downloaded = await gateway.download_item("blob-1")
    exported = await gateway.export_item("doc-1", "text/markdown")

    assert downloaded == b"downloaded"
    assert exported == b"exported"
    assert files.get_media_arguments == {"fileId": "blob-1", "supportsAllDrives": True}
    assert files.export_media_arguments == {
        "fileId": "doc-1",
        "mimeType": "text/markdown",
    }


@pytest.mark.anyio
async def test_gateway_enumerates_every_folder_in_one_paginated_query() -> None:
    def folder(identifier: str, parent: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "name": identifier.title(),
            "mimeType": "application/vnd.google-apps.folder",
            "driveId": "drive-1",
            "parents": [parent],
        }

    files = RecordingFiles(
        {},
        list_responses={
            None: {"files": [folder("plans", "root")], "nextPageToken": "page-2"},
            "page-2": {"files": [folder("archive", "plans")]},
        },
    )
    gateway = make_gateway(files)

    folders = await gateway.list_folders()

    assert [item.id for item in folders] == ["plans", "archive"]
    first_request = files.list_arguments[0]
    assert first_request["q"] == (
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    assert first_request["corpora"] == "user"
    assert "driveId" not in first_request
    assert first_request["includeItemsFromAllDrives"] is True
    assert first_request["pageSize"] == 1000
    # The caller rebuilds the hierarchy from `parents`, so ordering would buy nothing.
    assert "orderBy" not in first_request


@pytest.mark.anyio
async def test_shared_drive_queries_never_address_the_drive_itself() -> None:
    """`corpora=drive` with a `driveId` asks Drive for the drive itself, and Drive
    refuses that for an identity granted one folder inside it: 403
    `teamDriveMembershipRequired`, measured with no parent filter and again with
    the 59-parent filter a search sends. `corpora=user` with
    `includeItemsFromAllDrives` answered every query identically for a drive
    member and for the grantee, so membership is never required. The Shared
    Drive ID is still asserted, on every item, by `DriveLocation.contains`."""
    files = RecordingFiles({}, list_responses={None: {"files": []}})
    gateway = make_gateway(files)

    await gateway.list_children("root")
    await gateway.list_folders()
    await gateway.list_descendants(("root",))
    await gateway.search_items(("root",), "anything", limit=10)

    assert len(files.list_arguments) == 4
    for request in files.list_arguments:
        assert request["corpora"] == "user"
        assert request["includeItemsFromAllDrives"] is True
        assert request["supportsAllDrives"] is True
        assert "driveId" not in request


def http_error(status: int, *, reason: str | None = None, message: str = "boom") -> HttpError:
    """An `HttpError` shaped the way Drive actually sends one.

    The machine-readable code lives in `error.errors[].reason`; `HttpError.reason`
    is the human message. Building the real thing rather than a stub keeps this
    honest about which of the two the mapping reads.
    """
    error_body: dict[str, Any] = {"code": status, "message": message}
    body: dict[str, Any] = {"error": error_body}
    if reason is not None:
        error_body["errors"] = [{"reason": reason, "message": message}]
    return HttpError(
        httplib2.Response({"status": status}), json.dumps(body).encode(), uri="https://drive"
    )


def failing_gateway(error: Exception) -> tuple[GoogleDriveGateway, RecordingFiles]:
    files = RecordingFiles(error, list_responses={None: error})
    return make_gateway(files), files


@pytest.mark.anyio
async def test_a_missing_item_is_reported_as_not_found() -> None:
    """404 and "exists but invisible to this identity" are the same answer on
    purpose: telling them apart would confirm items outside the corpus."""
    gateway, _ = failing_gateway(http_error(404, reason="notFound"))

    with pytest.raises(DriveNotFound):
        await gateway.get_item("missing")


@pytest.mark.anyio
async def test_an_export_size_limit_is_distinguished_from_other_refusals() -> None:
    """Drive sends this as a 403, which is otherwise a permission failure. The
    caller can act on "too large" and cannot act on "forbidden"."""
    gateway, _ = failing_gateway(http_error(403, reason="exportSizeLimitExceeded"))

    with pytest.raises(ExportTooLarge):
        await gateway.export_item("doc-1", "text/markdown")


@pytest.mark.anyio
async def test_any_other_failure_carries_the_status_and_reason_forward() -> None:
    """An unmapped `HttpError` reaches a model as a bare "Error executing tool",
    which is indistinguishable from the tool itself being broken."""
    gateway, _ = failing_gateway(http_error(500, reason="backendError"))

    with pytest.raises(DriveUnavailable) as raised:
        await gateway.get_item("file-1")

    assert raised.value.status == 500
    assert raised.value.reason == "backendError"


@pytest.mark.anyio
async def test_a_rejected_token_is_reported_as_a_credentials_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one failure that is an operator's problem rather than a caller's:
    every tool fails together and no retry helps, so it is logged with a
    key a metric can count."""
    gateway, _ = failing_gateway(RefreshError("invalid_grant"))

    with caplog.at_level(logging.ERROR), pytest.raises(CredentialsRejected):
        await gateway.get_item("file-1")

    (record,) = [entry for entry in caplog.records if entry.levelno == logging.ERROR]
    assert getattr(record, "event", None) == "credentials_rejected"


@pytest.mark.anyio
async def test_a_401_is_a_credentials_failure_too() -> None:
    gateway, _ = failing_gateway(http_error(401, reason="authError"))

    with pytest.raises(CredentialsRejected):
        await gateway.get_item("file-1")


@pytest.mark.anyio
async def test_every_request_asks_the_client_to_retry() -> None:
    """The discovery client's own backoff covers 5xx, 429 and the rate-limit
    403 reasons. Not passing this was the only reason it never ran."""
    files = RecordingFiles({"id": "f", "name": "F", "mimeType": "text/plain"})
    gateway = make_gateway(files)

    await gateway.get_item("f")

    assert [call["num_retries"] for call in files.execute_calls] == [drive.NUM_RETRIES]


@pytest.mark.anyio
async def test_transfers_get_a_longer_timeout_than_metadata_calls() -> None:
    """An export of a large presentation legitimately takes minutes; a metadata
    call that has not answered in 30s is not going to."""
    files = RecordingFiles({}, list_responses={None: {"files": []}})
    gateway = make_gateway(files)

    await gateway.list_children("folder-1")
    await gateway.download_item("blob-1")

    listing, download = files.execute_calls
    assert listing["http"].http.timeout == drive.METADATA_TIMEOUT_SECONDS
    assert download["http"].http.timeout == drive.DOWNLOAD_TIMEOUT_SECONDS


def test_each_worker_thread_gets_its_own_transport_over_one_credential() -> None:
    """`asyncio.to_thread` runs requests on arbitrary workers, and the
    `httplib2.Http` inside a built service is not thread-safe — two threads
    sharing one interleave a response into the wrong request."""
    credentials = UserCredentials(None)
    pool = drive._PerThreadHttp(credentials, timeout=30)
    seen: list[Any] = []

    def take_one() -> None:
        seen.append(pool.get())

    threads = [threading.Thread(target=take_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first, second = seen
    assert first is not second
    assert first.credentials is second.credentials is credentials


def test_the_same_thread_reuses_its_transport() -> None:
    """Otherwise every request would open a fresh connection pool."""
    pool = drive._PerThreadHttp(UserCredentials(None), timeout=30)

    assert pool.get() is pool.get()


@pytest.mark.anyio
async def test_a_search_that_outruns_its_page_budget_says_so() -> None:
    """A caller that reports "no results" from a partial search is making a
    claim it cannot support."""
    endless = {"files": [], "nextPageToken": "more"}
    files = RecordingFiles({}, list_responses=dict.fromkeys([None, "more"], endless))
    gateway = make_gateway(files)

    page = await gateway.search_items(("root",), "anything", limit=10)

    assert page.incomplete is True
    assert page.has_more is True
    assert len(files.list_arguments) == drive.SEARCH_PAGE_BUDGET


@pytest.mark.anyio
async def test_drives_own_incomplete_search_is_surfaced() -> None:
    """Drive says when it could not reach part of the corpus. Sticky across
    pages: one incomplete page makes the whole answer incomplete."""
    files = RecordingFiles(
        {},
        list_responses={
            None: {"files": [], "incompleteSearch": True, "nextPageToken": "page-2"},
            "page-2": {"files": []},
        },
    )
    gateway = make_gateway(files)

    page = await gateway.search_items(("root",), "anything", limit=10)

    assert page.incomplete is True


@pytest.mark.anyio
async def test_the_search_asks_drive_whether_its_answer_was_complete() -> None:
    files = RecordingFiles({}, list_responses={None: {"files": []}})
    gateway = make_gateway(files)

    await gateway.search_items(("root",), "anything", limit=10)

    assert "incompleteSearch" in files.list_arguments[0]["fields"]


@pytest.mark.anyio
async def test_a_folder_enumeration_that_outruns_its_budget_fails_loudly() -> None:
    """A short folder map is *safe* — every folder missing from it becomes a
    refusal, never a leak — but it is silently wrong, and the corpus that
    outgrew the budget needs an operator, not a quietly shrinking view."""
    endless = {"files": [], "nextPageToken": "more"}
    files = RecordingFiles({}, list_responses=dict.fromkeys([None, "more"], endless))
    gateway = make_gateway(files)

    with pytest.raises(EnumerationBudgetExceeded, match="within 20 pages"):
        await gateway.list_folders()

    assert len(files.list_arguments) == drive.FOLDER_PAGE_BUDGET


@pytest.mark.anyio
async def test_an_enumeration_drive_calls_incomplete_says_so_rather_than_blaming_the_budget() -> (
    None
):
    """Drive can answer a complete-looking page with `incompleteSearch: true`:
    part of what the identity can see was not searched in time. That is a
    different failure from outgrowing the page budget — transient where the
    budget is structural — and reporting it as "did not complete within 20
    pages" sends whoever reads it hunting for a reach that is not there."""
    files = RecordingFiles({}, list_responses={None: {"files": [], "incompleteSearch": True}})
    gateway = make_gateway(files)

    with pytest.raises(EnumerationBudgetExceeded, match="incompleteSearch") as raised:
        await gateway.list_folders()

    assert "20 pages" not in str(raised.value)


@pytest.mark.anyio
async def test_the_gateway_can_say_which_identity_it_is_using() -> None:
    """A credential carries no name. The census that runs as the developer
    instead of the bot user enumerates a different reach and describes a
    different deployment, and nothing in its output says so unless this does."""
    about = RecordingAbout({"user": {"emailAddress": "bot@example.org"}}, [])
    files = RecordingFiles({})
    gateway = GoogleDriveGateway(RecordingService(files, about), credentials=UserCredentials(None))

    assert await gateway.identity() == "bot@example.org"
    assert about.get_arguments == {"fields": "user(emailAddress)"}


@pytest.mark.anyio
async def test_descendants_are_listed_by_parent_without_a_keyword() -> None:
    """The census needs the corpus itself, not a keyword slice of it."""
    report = {"id": "file-1", "name": "Report", "mimeType": "text/plain", "parents": ["nested"]}
    files = RecordingFiles({}, list_responses={None: {"files": [report]}})
    gateway = make_gateway(files)

    items = await gateway.list_descendants(("root", "nested"))

    assert [item.id for item in items] == ["file-1"]
    [request] = files.list_arguments
    assert "('root' in parents or 'nested' in parents)" in request["q"]
    assert "fullText" not in request["q"]


@pytest.mark.parametrize("parent_ids", [(), tuple(f"f{index}" for index in range(401))])
@pytest.mark.anyio
async def test_a_parent_batch_outside_drives_limit_is_refused(
    parent_ids: tuple[str, ...],
) -> None:
    """Drive caps a query at 400 parent clauses; the caller batches."""
    gateway = make_gateway(RecordingFiles({}))

    with pytest.raises(ValueError, match="between 1 and 400"):
        await gateway.search_items(parent_ids, "anything", limit=10)


def _hit(index: int) -> dict[str, Any]:
    return {
        "id": f"file-{index}",
        "name": f"Hit {index}",
        "mimeType": "text/plain",
        "parents": ["root"],
    }


@pytest.mark.anyio
async def test_a_search_asks_for_a_page_sized_to_the_request() -> None:
    """Asking for a thousand to serve ten paid for a count nobody sees. The page is
    the request, and Drive's continuation token says whether more matched."""
    files = RecordingFiles(
        {},
        list_responses={None: {"files": [_hit(i) for i in range(11)], "nextPageToken": "more"}},
    )
    gateway = make_gateway(files)

    page = await gateway.search_items(("root",), "anything", limit=11)

    [request] = files.list_arguments
    assert request["pageSize"] == 11
    assert len(page.items) == 11
    assert page.has_more is True
    assert page.incomplete is False


@pytest.mark.anyio
async def test_a_search_keeps_paging_when_drive_returns_a_short_page() -> None:
    """Drive may hand back fewer than `pageSize` items and still offer a next page.
    The request is only satisfied when the count is met or the token is gone."""
    files = RecordingFiles(
        {},
        list_responses={
            None: {"files": [_hit(0)], "nextPageToken": "page-2"},
            "page-2": {"files": [_hit(1), _hit(2)]},
        },
    )
    gateway = make_gateway(files)

    page = await gateway.search_items(("root",), "anything", limit=3)

    assert [item.id for item in page.items] == ["file-0", "file-1", "file-2"]
    assert len(files.list_arguments) == 2
    assert page.has_more is False


@pytest.mark.anyio
async def test_a_search_stops_once_the_request_is_met() -> None:
    files = RecordingFiles(
        {},
        list_responses={
            None: {"files": [_hit(0), _hit(1)], "nextPageToken": "page-2"},
            "page-2": {"files": [_hit(2)], "nextPageToken": "page-3"},
        },
    )
    gateway = make_gateway(files)

    page = await gateway.search_items(("root",), "anything", limit=3)

    assert len(page.items) == 3
    assert len(files.list_arguments) == 2
    assert page.has_more is True


@pytest.mark.anyio
async def test_a_search_page_never_exceeds_drives_maximum() -> None:
    files = RecordingFiles({}, list_responses={None: {"files": []}})
    gateway = make_gateway(files)

    await gateway.search_items(("root",), "anything", limit=5_000)

    assert files.list_arguments[0]["pageSize"] == drive.PAGE_SIZE


@pytest.mark.anyio
async def test_a_search_refuses_a_request_for_nothing() -> None:
    files = RecordingFiles({}, list_responses={None: {"files": []}})
    gateway = make_gateway(files)

    with pytest.raises(ValueError):
        await gateway.search_items(("root",), "anything", limit=0)


@pytest.mark.anyio
async def test_gateway_reads_who_owns_and_who_last_touched_an_item() -> None:
    """A citation needs a person. Drive leaves `owners` empty for shared-drive
    items by its own rule, so the last modifying user is requested alongside."""
    files = RecordingFiles(
        {
            "id": "file-1",
            "name": "Report",
            "mimeType": "text/plain",
            "driveId": "drive-1",
            "parents": ["root-1"],
            "capabilities": {"canDownload": True},
            "owners": [{"displayName": "Ada Lovelace", "emailAddress": "ada@example.org"}],
            "lastModifyingUser": {"emailAddress": "grace@example.org"},
        }
    )
    gateway = make_gateway(files)

    item = await gateway.get_item("file-1")

    assert item.owners == ("Ada Lovelace",)
    assert item.last_modified_by == "grace@example.org"
    assert files.get_arguments is not None
    assert "owners(displayName,emailAddress)" in files.get_arguments["fields"]
    assert "lastModifyingUser(displayName,emailAddress)" in files.get_arguments["fields"]
