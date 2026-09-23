"""Service account credentials, from the environment or a key file.

A container has no good place to keep a key file, so the whole key arrives as
one environment variable. A laptop has no convenient place to paste a multi-line
secret, so the file still works. The variable wins when both are present.

The validation here matches `credentials_json()` in api/settings.py
deliberately: the two packages deploy and run independently -- the sync must
work with no `api` package present at all -- so the rules are duplicated rather
than shared. A test asserts the two agree, so they cannot drift apart quietly.
"""

from __future__ import annotations

import json
import os

ENV_VAR = "GOOGLE_CREDENTIALS_JSON"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class CredentialsError(RuntimeError):
    """No usable credentials, or the ones supplied are malformed."""


def from_env():
    """The parsed key from GOOGLE_CREDENTIALS_JSON, or None if it is unset.

    Raises CredentialsError if the variable is set but unusable -- a typo in a
    deployment secret should say so, not silently fall through to a file that
    is not there either.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None

    try:
        info = json.loads(raw)
    except ValueError as err:
        raise CredentialsError(
            f"{ENV_VAR} is set but is not valid JSON ({err}). It should be the "
            f"entire contents of the service account key file, including the "
            f"outer braces."
        ) from err

    if not isinstance(info, dict) or "client_email" not in info:
        raise CredentialsError(
            f"{ENV_VAR} does not look like a service account key (no "
            f"client_email)."
        )
    return info


def load(credentials_path=None, scopes=None):
    """google.oauth2 Credentials, from the environment or the given file.

    Raises CredentialsError with an actionable message when neither is usable.
    """
    from google.oauth2.service_account import Credentials

    scopes = scopes or SCOPES

    info = from_env()
    if info:
        return Credentials.from_service_account_info(info, scopes=scopes)

    if credentials_path and os.path.exists(credentials_path):
        return Credentials.from_service_account_file(credentials_path, scopes=scopes)

    raise CredentialsError(
        f"No credentials. Set {ENV_VAR} to the contents of the service account "
        f"key file, or put the key at {credentials_path or 'credentials.json'}."
    )


def describe():
    """Where the credentials would come from, for logging. Never the key itself."""
    try:
        info = from_env()
    except CredentialsError as err:
        return f"{ENV_VAR} (invalid: {err})"
    if info:
        return f"{ENV_VAR} ({info.get('client_email', 'unknown account')})"
    return "key file"
