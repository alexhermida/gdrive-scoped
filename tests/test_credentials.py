from __future__ import annotations

from collections.abc import Sequence

import pytest
from google.auth.credentials import Credentials
from google.oauth2.credentials import Credentials as UserCredentials

from gdrive_scoped import credentials as credentials_module
from gdrive_scoped.credentials import (
    DRIVE_READONLY_SCOPE,
    TOKEN_URI,
    adc_credentials,
    refresh_token_credentials,
)


def bot_user_credentials() -> UserCredentials:
    return refresh_token_credentials(
        client_id="client-1", client_secret="secret-1", refresh_token="refresh-1"
    )


def test_refresh_token_credentials_are_fully_specified_by_their_arguments() -> None:
    """Nothing is discovered from the environment: a deployment's credential
    has to be reproducible from the three values an operator stored."""
    built = bot_user_credentials()

    assert isinstance(built, UserCredentials)
    assert built.refresh_token == "refresh-1"
    assert built.client_id == "client-1"
    assert built.client_secret == "secret-1"
    assert built.token_uri == TOKEN_URI


def test_refresh_token_credentials_hold_no_access_token_at_rest() -> None:
    """google-auth mints the first access token on the first request. The
    library never stores one, so nothing long-lived carries a bearer token."""
    assert bot_user_credentials().token is None


def test_refresh_token_credentials_never_consult_application_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that meant to use its bot user must fail loudly rather
    than silently picking up whatever identity the host happens to offer."""

    def fail(scopes: Sequence[str] | None = None, **_: object) -> tuple[Credentials, str | None]:
        raise AssertionError("google.auth.default must not be called")

    monkeypatch.setattr(credentials_module.google.auth, "default", fail)

    assert bot_user_credentials().refresh_token == "refresh-1"


def test_a_refresh_token_credential_is_scoped_to_read_only_drive() -> None:
    """The scope is the outermost bound on what a leak could reach; `drive.file`
    or `cloud-platform` creeping in would widen it silently."""
    assert list(bot_user_credentials().scopes or ()) == [DRIVE_READONLY_SCOPE]


def test_adc_asks_for_read_only_drive_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What ADC hands back is the machine's business; what this asks for is
    this library's, and it is the half worth pinning."""
    requested: list[Sequence[str] | None] = []

    def fake_default(
        scopes: Sequence[str] | None = None, **_: object
    ) -> tuple[Credentials, str | None]:
        requested.append(scopes)
        return UserCredentials(None), None

    monkeypatch.setattr(credentials_module.google.auth, "default", fake_default)

    adc_credentials()

    assert requested == [[DRIVE_READONLY_SCOPE]]
