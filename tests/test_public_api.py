from __future__ import annotations

import subprocess
import sys

import gdrive_scoped


def test_the_core_does_not_import_the_mcp_sdk() -> None:
    """The seam the whole layout exists for.

    An embedding consumer — a hosted provider, an agent — depends on the core
    and must not inherit the SDK, its transports or its pins. Run in a
    subprocess because `examples/mcp_server.py` imports the SDK and this
    session may have loaded it, so checking `sys.modules` in-process would
    prove nothing.
    """

    probe = (
        "import sys, gdrive_scoped; "
        "print(sorted(m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"


def test_every_exported_name_resolves() -> None:
    """`__all__` drifting from what the module actually defines makes
    `from gdrive_scoped import *` fail for a consumer and nobody else."""

    missing = [name for name in gdrive_scoped.__all__ if not hasattr(gdrive_scoped, name)]

    assert missing == []


def test_the_core_does_not_read_the_environment() -> None:
    """`gdrive_scoped.env` is for this repository's own entry points.

    A consumer configures the library by argument; if a core module reached
    for `env` instead, importing the library would start depending on
    variables the consumer never set — and one process could no longer serve
    two corpora.
    """

    probe = "import sys, gdrive_scoped; print('gdrive_scoped.env' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False"
