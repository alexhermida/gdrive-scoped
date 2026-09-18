"""Narrow read-only boundary for Google Drive."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httplib2
from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gdrive_scoped.errors import (
    CredentialsRejected,
    DriveNotFound,
    DriveUnavailable,
    EnumerationBudgetExceeded,
    ExportTooLarge,
)

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
FILE_FIELDS = (
    "id,name,mimeType,driveId,parents,trashed,modifiedTime,size,webViewLink,"
    "capabilities(canDownload),owners(displayName,emailAddress),"
    "lastModifyingUser(displayName,emailAddress)"
)

#: Handed to `request.execute`, which retries 5xx, 429 and the rate-limit 403
#: reasons with exponential backoff. The discovery client already implements
#: the loop; not passing this was the only reason it never ran.
NUM_RETRIES = 3

#: Two ceilings, because the two classes of request fail differently: a
#: metadata call that has not answered in 30s is not going to, while an export
#: of a large presentation legitimately takes minutes.
METADATA_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 120

PAGE_SIZE = 1000

#: Search pages are sized to the request, so this is only reached when Drive
#: keeps answering short pages with a continuation token. Stop and say the view
#: was partial rather than chasing it indefinitely.
SEARCH_PAGE_BUDGET = 5

#: The folder enumeration must be complete to be correct, so its budget is a
#: failure rather than a truncation. 20 pages is 20,000 folders.
FOLDER_PAGE_BUDGET = 20


@dataclass(frozen=True, slots=True)
class DriveItem:
    """Drive metadata used by the folder-scoped application core."""

    id: str
    name: str
    mime_type: str
    drive_id: str | None
    parents: tuple[str, ...] = ()
    trashed: bool = False
    can_download: bool = True
    modified_time: str | None = None
    size: int | None = None
    web_view_link: str | None = None
    #: Display names, or addresses when Drive gives no name. Empty for items in a
    #: Shared Drive by Drive's own rule: the drive owns them, nobody else does.
    owners: tuple[str, ...] = ()
    #: The one person field Drive fills in both locations, and the one a
    #: citation usually wants.
    last_modified_by: str | None = None


@dataclass(frozen=True, slots=True)
class SearchPage:
    """Search results, and whether they are the whole answer."""

    items: list[DriveItem]
    #: True when this did not see every match: either Drive set
    #: `incompleteSearch` — some corpus it could not reach in time — or the
    #: page budget stopped the walk. A caller that reports "no results" from an
    #: incomplete search is making a claim it cannot support.
    incomplete: bool = False
    #: Drive offered a further page after the requested count was met. Distinct
    #: from `incomplete`: the corpus was fully visible, the caller asked for less.
    has_more: bool = False
    #: Which kind of incomplete: the page budget ran out. False with `incomplete`
    #: set means Drive itself said `incompleteSearch` — a transient condition,
    #: where the budget is a structural one, and they need different responses.
    page_budget_exhausted: bool = False


class DriveGateway(Protocol):
    """The smallest external API needed by the scoped application boundary."""

    async def get_item(self, item_id: str) -> DriveItem:
        """Fetch current metadata for one Drive item."""

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        """List every current direct child of one folder."""

    async def list_folders(self) -> list[DriveItem]:
        """List every current folder the identity can see; the caller keeps the location's."""

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        """List every current non-folder item whose direct parent is named."""

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int
    ) -> SearchPage:
        """Keyword-search non-folder items whose direct parents are named.

        Returns at least `limit` matches when that many exist, in Drive's
        relevance order, and says whether more were on offer.
        """

    async def download_item(self, item_id: str) -> bytes:
        """Download one non-Google Workspace file."""

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        """Export one Google Workspace file to a supported MIME type."""


class _PerThreadHttp:
    """One `httplib2.Http` per worker thread, sharing one credential.

    `asyncio.to_thread` runs each request on an arbitrary worker, and the
    `httplib2.Http` inside a `build()`ed service is **not** thread-safe: it
    keeps a connection pool and a response buffer that two threads will
    interleave. In a serial stdio process that never shows; on a server with
    concurrent sessions it is a real race, and it surfaces as a response
    delivered to the wrong request.

    The credential is deliberately shared, not copied: `AuthorizedHttp` locks
    around refresh, so one token is minted and every thread waits for it,
    rather than each thread starting its own refresh on a cold start.
    """

    def __init__(self, credentials: Credentials, timeout: int) -> None:
        self._credentials = credentials
        self._timeout = timeout
        self._local = threading.local()

    def get(self) -> AuthorizedHttp:
        http = getattr(self._local, "http", None)
        if http is None:
            http = AuthorizedHttp(self._credentials, http=httplib2.Http(timeout=self._timeout))
            self._local.http = http
        return http


class GoogleDriveGateway:
    """Read-only adapter over the official Google Drive discovery client."""

    def __init__(self, service: Any, credentials: Credentials) -> None:
        self._service = service
        self._metadata_http = _PerThreadHttp(credentials, METADATA_TIMEOUT_SECONDS)
        self._transfer_http = _PerThreadHttp(credentials, DOWNLOAD_TIMEOUT_SECONDS)

    async def get_item(self, item_id: str) -> DriveItem:
        request = self._service.files().get(
            fileId=item_id,
            supportsAllDrives=True,
            fields=FILE_FIELDS,
        )
        response = await self._execute(request, self._metadata_http)
        return _parse_item(cast("Mapping[str, Any]", response))

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        escaped_folder_id = _escape_query_literal(folder_id)
        query = (
            f"'{escaped_folder_id}' in parents and trashed = false "
            f"and mimeType != '{SHORTCUT_MIME_TYPE}'"
        )
        page = await self._list_items(
            query, order_by="name_natural", page_budget=FOLDER_PAGE_BUDGET
        )
        _require_complete(page, "Folder listing")
        return page.items

    async def list_folders(self) -> list[DriveItem]:
        query = f"mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
        # Unordered on purpose: the caller rebuilds the hierarchy from `parents`, so the
        # order Drive returns folders in cannot change the result. Sorting a thousand-row
        # page would only cost Drive time.
        page = await self._list_items(query, page_budget=FOLDER_PAGE_BUDGET)
        _require_complete(page, "Folder enumeration")
        return page.items

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        """Every non-folder item directly under the named parents.

        The census and the evaluation harness need the corpus itself rather
        than a keyword slice of it, which is `search_items` without the
        `fullText` clause. Same 400-parent batching contract as search.
        """

        query = f"({_parents_clause(parent_ids)}) and trashed = false "
        query += f"and mimeType != '{FOLDER_MIME_TYPE}' and mimeType != '{SHORTCUT_MIME_TYPE}'"
        page = await self._list_items(query, page_budget=FOLDER_PAGE_BUDGET)
        _require_complete(page, "Corpus enumeration")
        return page.items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int
    ) -> SearchPage:
        if limit < 1:
            raise ValueError("Drive search requires a limit of at least 1")
        escaped_keywords = _escape_query_literal(keywords)
        query = (
            f"({_parents_clause(parent_ids)}) and trashed = false "
            f"and mimeType != '{FOLDER_MIME_TYPE}' "
            f"and mimeType != '{SHORTCUT_MIME_TYPE}' "
            f"and fullText contains '{escaped_keywords}'"
        )
        # No ordering: Drive rejects any files.list that combines `fullText` with
        # `orderBy`, and the relevance order it returns instead is the only ranking a
        # keyword search has. `ScopedDrive.search` keeps it, merging parent batches
        # by rank position.
        return await self._list_items(query, page_budget=SEARCH_PAGE_BUDGET, wanted=limit)

    async def identity(self) -> str:
        """The address of the Drive Identity these credentials belong to.

        Not part of `DriveGateway`: the boundary never needs it. Entry points
        do — a census run as the developer instead of the bot user enumerates
        a different reach and describes a different deployment, and a
        credential carries no name of its own.
        """

        request = self._service.about().get(fields="user(emailAddress)")
        response = await self._execute(request, self._metadata_http)
        user = cast("Mapping[str, Any]", response).get("user", {})
        return str(cast("Mapping[str, Any]", user).get("emailAddress", "unknown"))

    async def download_item(self, item_id: str) -> bytes:
        request = self._service.files().get_media(fileId=item_id, supportsAllDrives=True)
        content = await self._execute(request, self._transfer_http)
        return bytes(content)

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        request = self._service.files().export_media(fileId=item_id, mimeType=mime_type)
        content = await self._execute(request, self._transfer_http)
        return bytes(content)

    async def _list_items(
        self,
        query: str,
        *,
        page_budget: int,
        order_by: str | None = None,
        wanted: int | None = None,
    ) -> SearchPage:
        """Page through one query.

        With `wanted`, the page is sized to it and paging stops as soon as that
        many distinct items are in hand: the caller asked for a handful, and a
        thousand-row page was paying for a count nobody sees. Drive may still
        answer a short page with a continuation token, so "stop" means the count
        is met or the token is gone, whichever comes first. Without `wanted`, the
        query is enumerated whole.
        """

        page_size = PAGE_SIZE if wanted is None else min(wanted, PAGE_SIZE)
        page_token: str | None = None
        items_by_id: dict[str, DriveItem] = {}
        incomplete = False
        for _ in range(page_budget):
            # Never `corpora=drive` with a `driveId`: that addresses the drive
            # itself, which Drive allows only to a *member* of it. An identity
            # granted one folder inside a Shared Drive gets 403
            # `teamDriveMembershipRequired` — measured with no parent filter, and
            # again with the 59-parent filter a search sends. The `user` corpus
            # with `includeItemsFromAllDrives` answered every query identically
            # for a member and for that grantee (ADR 0010). The Shared Drive ID is
            # still asserted on every item, by `DriveLocation.contains`.
            #
            # `includeItemsFromAllDrives` is unconditional because the gateway no
            # longer knows the location: it is measured from the Configured Root
            # Folder's own `driveId` by `ScopedDrive.initialize`, one layer up.
            # Widening the request cannot widen the corpus — `DriveLocation.contains`
            # asserts the measured drive on every item enumerated, listed, searched
            # or read, so a My Drive corpus drops the Shared Drive rows this now
            # returns. What it costs is those rows: enumeration is bounded by the
            # identity's reach (ADR 0010), and for a My Drive corpus that reach now
            # includes any Shared Drive the identity can see.
            list_arguments: dict[str, Any] = {
                "corpora": "user",
                "includeItemsFromAllDrives": True,
            }
            if order_by is not None:
                list_arguments["orderBy"] = order_by

            request = self._service.files().list(
                q=query,
                spaces="drive",
                supportsAllDrives=True,
                pageSize=page_size,
                pageToken=page_token,
                fields=f"nextPageToken,incompleteSearch,files({FILE_FIELDS})",
                **list_arguments,
            )
            raw_response = await self._execute(request, self._metadata_http)
            response = cast("Mapping[str, Any]", raw_response)
            for raw_item in response.get("files", ()):
                item = _parse_item(cast("Mapping[str, Any]", raw_item))
                items_by_id.setdefault(item.id, item)

            # Drive's own admission that it could not reach part of the corpus.
            # Sticky across pages: one incomplete page makes the whole answer
            # incomplete, and a later complete page does not undo that.
            incomplete = incomplete or bool(response.get("incompleteSearch", False))

            raw_page_token = response.get("nextPageToken")
            if not raw_page_token:
                return SearchPage(items=list(items_by_id.values()), incomplete=incomplete)
            if wanted is not None and len(items_by_id) >= wanted:
                return SearchPage(
                    items=list(items_by_id.values()), incomplete=incomplete, has_more=True
                )
            page_token = str(raw_page_token)

        return SearchPage(
            items=list(items_by_id.values()),
            incomplete=True,
            has_more=True,
            page_budget_exhausted=True,
        )

    async def _execute(self, request: Any, http: _PerThreadHttp) -> Any:
        return await asyncio.to_thread(_execute_sync, request, http)


def create_gateway(credentials: Credentials) -> GoogleDriveGateway:
    """Build the read-only Drive adapter over credentials the caller supplies.

    Credentials are an argument rather than something discovered here, so the
    same gateway serves a local process on ADC and a deployment on a bot
    user's refresh token. See `gdrive_scoped.credentials` for both builders.

    Nothing else is needed: the gateway sends the same request shape wherever
    the corpus lives, and the location is measured from the root folder by
    `ScopedDrive`.
    """

    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return GoogleDriveGateway(service=service, credentials=credentials)


def _execute_sync(request: Any, http: _PerThreadHttp) -> Any:
    """Run one Drive request on this worker thread, mapping every failure.

    An unmapped `HttpError` reaching an embedding adapter arrives at the model
    as a bare "Error executing tool", which is indistinguishable from a bug in
    the tool itself.
    """

    try:
        return request.execute(num_retries=NUM_RETRIES, http=http.get())
    except RefreshError as error:
        # The bot user's refresh token is gone: revoked, expired under a
        # Testing consent screen, or invalidated by a password change. Every
        # tool fails together and no retry helps.
        logger.error(
            "Google rejected the stored credentials",
            extra={"event": "credentials_rejected"},
        )
        raise CredentialsRejected(
            "Google rejected the stored Drive credentials; the refresh token needs re-minting"
        ) from error
    except HttpError as error:
        raise _translate_http_error(error) from error


def _translate_http_error(error: HttpError) -> Exception:
    status = error.status_code
    reason = _drive_reason(error)
    if status == 404:
        # Deliberately the same answer as "exists but you cannot see it":
        # distinguishing them would confirm items outside the corpus.
        return DriveNotFound("Drive has no such item")
    if status == 403 and reason == "exportSizeLimitExceeded":
        return ExportTooLarge("Drive will not export a file this large")
    if status == 401:
        logger.error(
            "Google rejected the stored credentials",
            extra={"event": "credentials_rejected"},
        )
        return CredentialsRejected(
            "Google rejected the stored Drive credentials; the refresh token needs re-minting"
        )
    return DriveUnavailable(
        f"Drive returned {status or 'an error'}: {error.reason}", status=status, reason=reason
    )


def _drive_reason(error: HttpError) -> str | None:
    """Drive's machine-readable `reason`, which `HttpError.reason` is not.

    `HttpError.reason` is the human message; the code that distinguishes a
    quota failure from an export limit sits in `error_details`, which the
    client fills from `error.errors` when Drive sends that shape.
    """

    details = error.error_details
    if isinstance(details, list) and details and isinstance(details[0], dict):
        raw_reason = details[0].get("reason")
        return str(raw_reason) if raw_reason is not None else None
    return None


def _require_complete(page: SearchPage, what: str) -> None:
    """Refuse a partial enumeration rather than returning one.

    A short folder map is safe — every folder missing from it becomes a
    refusal, never a leak — but it is silently wrong, and the corpus that
    outgrew the budget needs an operator rather than a quietly shrinking view.
    """

    if page.page_budget_exhausted:
        raise EnumerationBudgetExceeded(
            f"{what} did not complete within {FOLDER_PAGE_BUDGET} pages: the identity "
            f"can see more than {FOLDER_PAGE_BUDGET * PAGE_SIZE:,} folders"
        )
    if page.incomplete:
        # Drive's own flag, on a walk that finished its pages. Under the `user`
        # corpus with items from all drives, Drive searches several stores and
        # may give up on one in time; the next attempt usually completes.
        raise EnumerationBudgetExceeded(
            f"{what} was reported incomplete by Drive (incompleteSearch): part of what "
            "the identity can see was not searched. Usually transient; run it again"
        )


def _parents_clause(parent_ids: tuple[str, ...]) -> str:
    if not parent_ids or len(parent_ids) > 400:
        raise ValueError("Drive search requires between 1 and 400 parent folders")
    return " or ".join(
        f"'{_escape_query_literal(parent_id)}' in parents" for parent_id in parent_ids
    )


def _parse_item(value: Mapping[str, Any]) -> DriveItem:
    capabilities = cast("Mapping[str, Any]", value.get("capabilities", {}))
    raw_size = value.get("size")
    return DriveItem(
        id=str(value["id"]),
        name=str(value["name"]),
        mime_type=str(value["mimeType"]),
        drive_id=str(value["driveId"]) if value.get("driveId") else None,
        parents=tuple(str(parent) for parent in value.get("parents", ())),
        trashed=bool(value.get("trashed", False)),
        can_download=bool(capabilities.get("canDownload", False)),
        modified_time=str(value["modifiedTime"]) if value.get("modifiedTime") else None,
        size=int(raw_size) if raw_size is not None else None,
        web_view_link=str(value["webViewLink"]) if value.get("webViewLink") else None,
        owners=tuple(
            name for name in (_person(owner) for owner in value.get("owners", ())) if name
        ),
        last_modified_by=_person(value.get("lastModifyingUser")),
    )


def _person(value: object) -> str | None:
    """A Drive user as one string: the display name, or the address without one."""

    if not isinstance(value, Mapping):
        return None
    name = value.get("displayName") or value.get("emailAddress")
    return str(name) if name else None


def _escape_query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
