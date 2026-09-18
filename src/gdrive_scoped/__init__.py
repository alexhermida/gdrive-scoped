"""Read-only, folder-scoped access to one Google Drive subtree.

The core is transport-agnostic and configuration-agnostic: it takes
credentials and a root folder as arguments, reads no environment variable, and
imports no MCP. Adapters sit over it — a hosted MCP provider, an agent, a
script — and none of them is required to use the library. `examples/` has a
small MCP server built this way.

Composing it by hand:

    from gdrive_scoped import DocumentService, ScopedDrive, create_gateway
    from gdrive_scoped.credentials import refresh_token_credentials

    gateway = create_gateway(refresh_token_credentials(...))
    scoped = ScopedDrive(gateway, root_folder_id)
    await scoped.initialize()          # also measures `scoped.location`
    service = DocumentService(scoped)

Which Drive the corpus is in is measured from the root folder rather than
configured: `initialize()` reads it, `ScopedDrive.location` reports it, and
every item is checked against it.

Failures are the hierarchy in `gdrive_scoped.errors`, all under `DriveError`.
"""

from gdrive_scoped.census import Census, format_census, take_census
from gdrive_scoped.documents import DocumentService, FolderPage, ReadResult
from gdrive_scoped.drive import DriveGateway, DriveItem, SearchPage, create_gateway
from gdrive_scoped.location import DriveKind, DriveLocation
from gdrive_scoped.scope import ScopedDrive, ScopedItem, SearchResult, audit_caller

__all__ = [
    # The measured location
    "DriveKind",
    "DriveLocation",
    # The Drive boundary
    "DriveGateway",
    "create_gateway",
    # The authorization boundary and the service over it
    "ScopedDrive",
    "DocumentService",
    # Values
    "DriveItem",
    "ScopedItem",
    "SearchPage",
    "SearchResult",
    "FolderPage",
    "ReadResult",
    # Measurement
    "Census",
    "take_census",
    "format_census",
    # Audit
    "audit_caller",
]
