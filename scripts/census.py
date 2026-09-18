#!/usr/bin/env python3
"""Count the configured corpus, without reading a single document.

    just census

Folder count and depth, file count, the MIME histogram, and the share any
extractor can currently read. Cheap enough to run against production, and the
only honest way to prioritise extractors — the formats a corpus holds are
never the ones anybody guesses.
"""

from __future__ import annotations

import asyncio

from gdrive_scoped import ScopedDrive, create_gateway, format_census, take_census
from gdrive_scoped.env import Settings, credentials_from_environ


async def main() -> int:
    settings = Settings.from_environ()
    gateway = create_gateway(credentials_from_environ())
    # Said first, because nothing else in the output would. A census run as
    # the developer instead of the bot user enumerates the developer's reach
    # and describes a deployment that does not exist.
    print(f"identity: {await gateway.identity()}")
    scoped_drive = ScopedDrive(
        gateway=gateway,
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    )
    await scoped_drive.initialize()
    print(format_census(await take_census(scoped_drive)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
