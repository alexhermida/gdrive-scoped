"""Run deterministic source-discovery evaluation against the configured Drive corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from gdrive_scoped.bench.evaluation import evaluate_retrieval, load_cases
from gdrive_scoped.documents import DocumentService
from gdrive_scoped.drive import create_gateway
from gdrive_scoped.env import Settings, credentials_from_environ
from gdrive_scoped.scope import ScopedDrive


async def _evaluate(path: Path) -> int:
    settings = Settings.from_environ()
    gateway = create_gateway(credentials_from_environ(), settings.location)
    # Said first, because nothing else in the output would. An evaluation run
    # as the developer instead of the bot user searches a different reach and
    # reports sources a deployment would never find.
    print(f"identity: {await gateway.identity()}")
    scoped_drive = ScopedDrive(
        gateway=gateway,
        location=settings.location,
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    )
    await scoped_drive.initialize()
    outcomes = await evaluate_retrieval(DocumentService(scoped_drive), load_cases(path))
    print(json.dumps([outcome.model_dump() for outcome in outcomes], indent=2))
    passed = sum(outcome.passed for outcome in outcomes)
    print(f"\n{passed}/{len(outcomes)} expected sources discovered")
    return 0 if passed == len(outcomes) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    arguments = parser.parse_args()
    return asyncio.run(_evaluate(arguments.questions))


if __name__ == "__main__":
    raise SystemExit(main())
