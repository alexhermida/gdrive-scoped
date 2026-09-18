from __future__ import annotations

import pytest
from google.oauth2.credentials import Credentials as UserCredentials

from gdrive_scoped.env import ConfigurationError, Settings, credentials_from_environ

CORPUS = {"GDRIVE_ROOT_FOLDER_ID": "folder-456"}
OAUTH = {
    "GDRIVE_OAUTH_CLIENT_ID": "client",
    "GDRIVE_OAUTH_CLIENT_SECRET": "secret",
    "GDRIVE_OAUTH_REFRESH_TOKEN": "refresh",
}


def test_configuration_describes_a_corpus_with_its_root_folder_alone() -> None:
    """One folder is the whole description. Where that folder lives is not
    configuration — `ScopedDrive.initialize()` measures it from the folder."""

    settings = Settings.from_environ({"GDRIVE_ROOT_FOLDER_ID": " folder-456 "})

    assert settings == Settings(root_folder_id="folder-456")


def test_configuration_ignores_the_drive_variables_it_no_longer_reads() -> None:
    """An operator upgrading has them in `.env`, and a stale assertion about
    where the root lives must neither be honoured nor complained about."""

    settings = Settings.from_environ(
        {
            "GDRIVE_ROOT_FOLDER_ID": "folder-456",
            "GDRIVE_DRIVE_KIND": "my_drive",
            "GDRIVE_SHARED_DRIVE_ID": "drive-123",
        }
    )

    assert settings == Settings(root_folder_id="folder-456")


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        # No default root folder: there is no folder it would be right to serve.
        ({}, "GDRIVE_ROOT_FOLDER_ID"),
        ({"GDRIVE_ROOT_FOLDER_ID": "  "}, "GDRIVE_ROOT_FOLDER_ID"),
        (CORPUS | {"GDRIVE_ROOT_FOLDER_ID": "root"}, "not the alias 'root'"),
        (CORPUS | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "soon"}, "expected seconds"),
        (CORPUS | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "-1"}, "must not be negative"),
        # float() accepts both; an infinite window would never refresh the folder map.
        (CORPUS | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "inf"}, "must be finite"),
        (CORPUS | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "nan"}, "must be finite"),
    ],
)
def test_configuration_fails_closed(environ: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environ(environ)


def test_the_folder_map_window_can_be_closed_entirely() -> None:
    settings = Settings.from_environ(CORPUS | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "0"})

    assert settings.folder_map_ttl_seconds == 0


def test_a_configured_refresh_token_builds_credentials_for_that_user() -> None:
    credentials = credentials_from_environ(CORPUS | OAUTH)

    assert isinstance(credentials, UserCredentials)
    assert credentials.refresh_token == "refresh"
    assert credentials.client_id == "client"
    # Never held at rest: google-auth mints the first access token on the
    # first request.
    assert credentials.token is None


@pytest.mark.parametrize("absent", sorted(OAUTH))
def test_a_partial_oauth_triple_is_an_error_rather_than_a_fallback(absent: str) -> None:
    """Falling back to ADC here would read the corpus as the developer rather
    than as the deployment's identity, and report a different boundary."""

    with pytest.raises(ConfigurationError, match=absent):
        credentials_from_environ({key: value for key, value in OAUTH.items() if key != absent})
