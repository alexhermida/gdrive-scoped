"""Google credentials, built explicitly and passed in.

The core takes a `Credentials` object; it never discovers one. That is what
lets the same core serve a local process using Application Default Credentials
and a hosted deployment using a bot user's refresh token, without either
knowing the other exists — and it is why nothing here reads an environment
variable.

Both builders request `drive.readonly` and nothing else.
"""

from __future__ import annotations

import google.auth
from google.auth.credentials import Credentials
from google.oauth2.credentials import Credentials as UserCredentials

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

#: Google's OAuth 2 token endpoint. Named rather than defaulted so the
#: credential below is fully specified by its arguments.
TOKEN_URI = "https://oauth2.googleapis.com/token"


def refresh_token_credentials(
    *, client_id: str, client_secret: str, refresh_token: str
) -> UserCredentials:
    """Build long-lived credentials for one user from a stored refresh token.

    The access token is deliberately `None`: the library never holds one at
    rest, and google-auth mints the first one on the first request. A refresh
    token minted under an *Internal* OAuth consent screen does not expire on a
    schedule, which is what makes this viable unattended; one minted under
    Testing + External dies in seven days and presents as a total outage.
    """

    return UserCredentials(
        None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[DRIVE_READONLY_SCOPE],
    )


def adc_credentials() -> Credentials:
    """Discover Application Default Credentials, for local use.

    The developer path: `GOOGLE_APPLICATION_CREDENTIALS` pointing at an
    authorized-user file, or whatever else ADC resolves to on the machine.
    """

    credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
    return credentials
