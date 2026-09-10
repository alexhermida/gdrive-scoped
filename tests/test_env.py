from __future__ import annotations

import pytest
from google.oauth2.credentials import Credentials as UserCredentials

from gdrive_scoped.env import ConfigurationError, Settings, credentials_from_environ
from gdrive_scoped.location import DriveKind, DriveLocation

SHARED = {
    "GDRIVE_DRIVE_KIND": "shared_drive",
    "GDRIVE_SHARED_DRIVE_ID": "drive-123",
    "GDRIVE_ROOT_FOLDER_ID": "folder-456",
}
OAUTH = {
    "GDRIVE_OAUTH_CLIENT_ID": "client",
    "GDRIVE_OAUTH_CLIENT_SECRET": "secret",
    "GDRIVE_OAUTH_REFRESH_TOKEN": "refresh",
}


def test_configuration_loads_one_shared_drive_root() -> None:
    settings = Settings.from_environ(
        {
            "GDRIVE_DRIVE_KIND": " shared_drive ",
            "GDRIVE_SHARED_DRIVE_ID": " drive-123 ",
            "GDRIVE_ROOT_FOLDER_ID": " folder-456 ",
        }
    )

    assert settings == Settings(
        location=DriveLocation(DriveKind.SHARED_DRIVE, shared_drive_id="drive-123"),
        root_folder_id="folder-456",
    )


def test_configuration_loads_one_my_drive_root() -> None:
    settings = Settings.from_environ(
        {"GDRIVE_DRIVE_KIND": "my_drive", "GDRIVE_ROOT_FOLDER_ID": "folder-456"}
    )

    assert settings == Settings(
        location=DriveLocation(DriveKind.MY_DRIVE), root_folder_id="folder-456"
    )


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({}, "GDRIVE_DRIVE_KIND, GDRIVE_ROOT_FOLDER_ID"),
        # No default kind: the wrong one does not fail, it scopes every query
        # to a drive the root is not in and reports an empty corpus.
        ({"GDRIVE_ROOT_FOLDER_ID": "folder-456"}, "GDRIVE_DRIVE_KIND"),
        (SHARED | {"GDRIVE_DRIVE_KIND": "personal"}, "expected shared_drive or my_drive"),
        (SHARED | {"GDRIVE_SHARED_DRIVE_ID": ""}, "GDRIVE_SHARED_DRIVE_ID"),
        (
            {
                "GDRIVE_DRIVE_KIND": "my_drive",
                "GDRIVE_ROOT_FOLDER_ID": "f",
                "GDRIVE_SHARED_DRIVE_ID": "d",
            },
            "must not be set when GDRIVE_DRIVE_KIND=my_drive",
        ),
        (SHARED | {"GDRIVE_ROOT_FOLDER_ID": "root"}, "not the alias 'root'"),
        (SHARED | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "soon"}, "expected seconds"),
        (SHARED | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "-1"}, "must not be negative"),
    ],
)
def test_configuration_fails_closed(environ: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_environ(environ)


def test_the_folder_map_window_can_be_closed_entirely() -> None:
    settings = Settings.from_environ(SHARED | {"GDRIVE_FOLDER_MAP_TTL_SECONDS": "0"})

    assert settings.folder_map_ttl_seconds == 0


def test_a_configured_refresh_token_builds_credentials_for_that_user() -> None:
    credentials = credentials_from_environ(SHARED | OAUTH)

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
