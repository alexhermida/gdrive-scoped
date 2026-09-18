#!/usr/bin/env python3
"""One way to put `gdrive_scoped` on a wire, for trying the library by hand.

    just example-stdio          # for an MCP host, or the Inspector
    just example-http           # loopback Streamable HTTP on :8000/mcp

An **example, not a product**: it is the shortest complete adapter, so a
reader can see what an embedding is responsible for. Deliberately outside
`src/` — the library imports no MCP in any configuration, and nothing here is
packaged or published.

What an adapter owes the library, all four visible below:

1. Build credentials and hand them in. The core discovers none.
2. `await scoped_drive.initialize()` once, before serving, and before anything
   else is asked of the scope. It proves the configured root exists and is a
   live folder, and measures which Drive it lives in — the location every later
   item is checked against.
3. Translate `DriveError` into whatever the wire calls a failure. Every
   deliberate refusal — an item outside the corpus above all — is in that
   hierarchy, and a caller that sees a bare "internal error" instead cannot
   tell "not in this corpus" from "the server is broken".
4. Keep the process to one corpus. The boundary is per-`ScopedDrive`.

Unauthenticated, so HTTP binds loopback only. Anything reachable by anyone
else needs an adapter that authenticates its callers, not this file.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import sys
from collections.abc import Awaitable

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from gdrive_scoped import DocumentService, ScopedDrive, create_gateway
from gdrive_scoped.documents import DEFAULT_READ_CHARS, FolderPage, ReadResult
from gdrive_scoped.env import ConfigurationError, Settings, credentials_from_environ
from gdrive_scoped.errors import DriveError
from gdrive_scoped.scope import ScopedItem, SearchResult

INSTRUCTIONS = """\
Read-only access to one configured Google Drive folder subtree.
Use focused keywords when searching, then read promising documents in bounded chunks.
"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def create_server(service: DocumentService) -> MCPServer:
    server = MCPServer(name="gdrive-scoped-example", instructions=INSTRUCTIONS)

    @server.tool(
        name="search_documents",
        description=(
            "Search the configured Drive folder and all descendants. "
            "Use a short, focused keyword or phrase rather than a full question."
        ),
        annotations=READ_ONLY,
    )
    async def search_documents(query: str, limit: int = 10) -> SearchResult:
        return await _translated(service.search_documents(query, limit))

    @server.tool(
        name="list_folder",
        description="List direct children of the configured root, or of a folder by its Drive ID.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def list_folder(
        folder_id: str | None = None, cursor: str | None = None, limit: int = 100
    ) -> FolderPage:
        return await _translated(service.list_folder(folder_id, cursor, limit))

    @server.tool(
        name="get_metadata",
        description="Get current metadata for one item in the corpus, by its Drive ID.",
        annotations=READ_ONLY,
    )
    async def get_metadata(file_id: str) -> ScopedItem:
        return await _translated(service.get_metadata(file_id))

    @server.tool(
        name="read_document",
        description=(
            "Read a bounded chunk of one document, by its Drive ID. "
            "Pass next_cursor to continue a large document."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def read_document(
        file_id: str, cursor: str | None = None, max_chars: int = DEFAULT_READ_CHARS
    ) -> ReadResult:
        return await _translated(service.read_document(file_id, cursor, max_chars))

    return server


async def _translated[T](operation: Awaitable[T]) -> T:
    """Point 3 above. `DriveError` covers the whole hierarchy, so a leaf added
    later still reaches the caller as words rather than as an unexplained
    "Error executing tool"."""

    try:
        return await operation
    except (DriveError, ValueError) as error:
        raise ToolError(str(error)) from error


def build_service(settings: Settings) -> DocumentService:
    scoped_drive = ScopedDrive(
        gateway=create_gateway(credentials_from_environ()),
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    )
    asyncio.run(scoped_drive.initialize())
    return DocumentService(scoped_drive)


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args(argv)

    if arguments.transport == "streamable-http" and not _is_loopback(arguments.host):
        parser.error("this example authenticates nobody, so HTTP is loopback only")

    try:
        settings = Settings.from_environ()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1

    server = create_server(build_service(settings))
    if arguments.transport == "stdio":
        # stdout is the protocol channel here: never print to it.
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=arguments.host,
            port=arguments.port,
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
