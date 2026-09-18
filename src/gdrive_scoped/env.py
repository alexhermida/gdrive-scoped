"""Configuration from the environment, for entry points only.

The core takes a root folder and credentials as arguments and reads nothing
from the environment — that is what lets one process embed it against several
corpora, and what keeps a consumer's configuration its own business. This
module is the convenience wrapper the repository's own entry points use: the
benchmark, the census script, the live tests and the example server. Nothing
under `gdrive_scoped` imports it, and `tests/test_public_api` holds that line.

There is nothing here about *where* the corpus lives, because that is not
configuration: `ScopedDrive.initialize()` measures the Drive location from the
root folder's own metadata.

The variable names are deliberately the ones a deployment already sets, so a
single environment file serves both a hosted provider and a local run.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

import google.auth
from google.auth.credentials import Credentials

from gdrive_scoped.credentials import DRIVE_READONLY_SCOPE, refresh_token_credentials
from gdrive_scoped.scope import DEFAULT_FOLDER_MAP_TTL_SECONDS

ROOT_FOLDER_ID_ENV = "GDRIVE_ROOT_FOLDER_ID"
FOLDER_MAP_TTL_ENV = "GDRIVE_FOLDER_MAP_TTL_SECONDS"
CLIENT_ID_ENV = "GDRIVE_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "GDRIVE_OAUTH_CLIENT_SECRET"  # noqa: S105 - a variable name
REFRESH_TOKEN_ENV = "GDRIVE_OAUTH_REFRESH_TOKEN"  # noqa: S105 - a variable name

_OAUTH_ENV = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REFRESH_TOKEN_ENV)


class ConfigurationError(ValueError):
    """Raised when the environment cannot describe one Drive scope."""


@dataclass(frozen=True, slots=True)
class Settings:
    """One corpus, as described by the environment.

    A corpus is one folder, so a root folder ID is the whole of its
    description. The Drive location it lives in used to be described here too,
    as a kind plus a Shared Drive ID that the library asserted the root against;
    it is now read off the root folder itself, where Drive already records it.
    """

    root_folder_id: str
    #: The staleness window for folder ancestry. Raising it widens the time a
    #: folder moved out of the corpus keeps serving its contents; 0 removes it.
    folder_map_ttl_seconds: float = DEFAULT_FOLDER_MAP_TTL_SECONDS

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings without defaulting the root folder.

        There is no sensible default for which folder a corpus is, and a
        defaulted one would silently serve somebody else's. Everything else is
        either optional or measured: where that folder lives is read from its
        own Drive metadata by `ScopedDrive.initialize()`.
        """

        values = os.environ if environ is None else environ
        root_folder_id = values.get(ROOT_FOLDER_ID_ENV, "").strip()

        if not root_folder_id:
            raise ConfigurationError(f"Missing required configuration: {ROOT_FOLDER_ID_ENV}")
        if root_folder_id == "root":
            raise ConfigurationError(
                f"{ROOT_FOLDER_ID_ENV} must be a folder ID, not the alias 'root'. "
                "The whole of a Drive is not a corpus."
            )

        return cls(
            root_folder_id=root_folder_id,
            folder_map_ttl_seconds=_read_folder_map_ttl(values),
        )


def credentials_from_environ(environ: Mapping[str, str] | None = None) -> Credentials:
    """A stored refresh token when one is configured, otherwise ADC.

    Both are the same identity seen from different sides: a deployment carries
    the bot user's refresh token, and a developer has whatever
    `gcloud auth application-default login` left behind. A *partial* OAuth
    triple is an error rather than a silent fall back to ADC, which would
    otherwise read the corpus as the wrong identity and quietly report a
    different boundary.
    """

    values = os.environ if environ is None else environ
    present = {name: values.get(name, "").strip() for name in _OAUTH_ENV}
    supplied = [name for name, value in present.items() if value]

    if not supplied:
        credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
        return credentials
    if len(supplied) < len(_OAUTH_ENV):
        absent = ", ".join(name for name in _OAUTH_ENV if name not in supplied)
        raise ConfigurationError(
            f"Incomplete OAuth configuration: {absent} not set. Set all three of "
            f"{', '.join(_OAUTH_ENV)}, or none of them to use Application Default "
            "Credentials."
        )
    return refresh_token_credentials(
        client_id=present[CLIENT_ID_ENV],
        client_secret=present[CLIENT_SECRET_ENV],
        refresh_token=present[REFRESH_TOKEN_ENV],
    )


def _read_folder_map_ttl(values: Mapping[str, str]) -> float:
    raw_ttl = values.get(FOLDER_MAP_TTL_ENV, "").strip()
    if not raw_ttl:
        return DEFAULT_FOLDER_MAP_TTL_SECONDS
    try:
        ttl_seconds = float(raw_ttl)
    except ValueError as error:
        raise ConfigurationError(f"Invalid {FOLDER_MAP_TTL_ENV}: expected seconds") from error
    # float() also parses "inf" and "nan". An infinite window never refreshes the
    # folder map, so a folder moved out of the corpus would keep serving its
    # contents for the life of the process; nan compares false with everything
    # and is no policy at all.
    if not math.isfinite(ttl_seconds):
        raise ConfigurationError(f"Invalid {FOLDER_MAP_TTL_ENV}: must be finite")
    if ttl_seconds < 0:
        raise ConfigurationError(f"Invalid {FOLDER_MAP_TTL_ENV}: must not be negative")
    return ttl_seconds
