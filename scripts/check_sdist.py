#!/usr/bin/env python3
"""Refuse a source distribution that carries anything outside the allowlist.

    uv build && python scripts/check_sdist.py dist/*.tar.gz

The wheel is bounded by `packages = ["src/gdrive_scoped"]`; the sdist is not. A
build from a working copy once shipped a local agent-settings file that only a
*global* gitignore was hiding, because hatchling honours the repository's ignore
file and not the user's. `pyproject.toml` now names what an sdist may contain;
this turns that list into a failing check rather than a habit.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import PurePosixPath

ALLOWED_TOP_LEVEL = frozenset(
    {
        "src",
        "tests",
        "docs",
        "examples",
        "scripts",
        "evaluation",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "SECURITY.md",
        "AGENTS.md",
        "CONTEXT.md",
        "justfile",
        "pyproject.toml",
        ".python-version",
        # Hatchling always adds the repository's ignore file; it is harmless in an archive.
        ".gitignore",
        "uv.lock",
        "PKG-INFO",
    }
)
#: Never acceptable anywhere in the archive, whatever directory they sit in.
FORBIDDEN_SUFFIXES = (".local.json", ".pem", ".key", ".p12", ".pyc")
FORBIDDEN_NAMES = frozenset({".env", "settings.local.json", "client_secret.json"})


def offending_entries(archive: str) -> list[str]:
    offending: list[str] = []
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts[1:]  # drop the versioned top directory
            if not parts:
                continue
            hidden = any(part.startswith(".") for part in parts[1:])
            if (
                parts[0] not in ALLOWED_TOP_LEVEL
                or hidden
                or parts[-1] in FORBIDDEN_NAMES
                or parts[-1].startswith(".env")
                or parts[-1].endswith(FORBIDDEN_SUFFIXES)
            ):
                offending.append(member.name)
    return offending


def main(archives: list[str]) -> int:
    if not archives:
        print("usage: check_sdist.py dist/*.tar.gz", file=sys.stderr)
        return 2
    failed = False
    for archive in archives:
        offending = offending_entries(archive)
        if offending:
            failed = True
            print(f"{archive}: refusing, unexpected entries:", file=sys.stderr)
            for name in offending:
                print(f"  {name}", file=sys.stderr)
        else:
            print(f"{archive}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
