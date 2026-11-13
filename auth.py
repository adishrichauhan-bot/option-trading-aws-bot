"""Fyers v3 authentication with browser login, token caching, and refresh.

The browser login and OTP remain interactive only when no usable cached token
exists. Tokens are stored outside the repository by default at
``~/.fyers/token.json``.
"""

from __future__ import annotations

import hashlib
import getpass
import json
import os
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


load_dotenv()

TOKEN_ENDPOINT = "https://api-t1.fyers.in/api/v3/validate-authcode"
REFRESH_ENDPOINT = "https://api-t1.fyers.in/api/v3/validate-refresh-token"


class FyersAuthenticationError(RuntimeError):
    """Interactive login/configuration is needed; never expose credential values."""


def trading_credentials() -> tuple[str, str]:
    """Validate or refresh credentials without browser prompts in the trading loop."""
    client_id = os.getenv("FYERS_CLIENT_ID") or os.getenv("client_id")
    if not client_id:
        raise FyersAuthenticationError("Fyers client ID is missing. Configure .env and run python auth.py.")
    cached = read_tokens()
    # An old environment override must not hide a newly generated cache token.
    candidates = dict.fromkeys((os.getenv("FYERS_ACCESS_TOKEN"), cached.get("access_token")))
    for token in candidates:
        if token and access_token_works(client_id, token):
            return client_id, token
    refresh_token = os.getenv("refresh_token") or cached.get("refresh_token")
    secret_key, pin = os.getenv("secret_key"), os.getenv("pin")
    if not (refresh_token and secret_key and pin):
        raise FyersAuthenticationError(
            "Fyers access token is unusable and automatic refresh needs a refresh token, secret_key and pin. "
            "Run python auth.py interactively on this server before starting the engine. "
            "A token file existing does not mean its token is valid."
        )
    try:
        refreshed = refresh_access_token(client_id, secret_key, refresh_token, pin)
        token = refreshed.get("access_token")
        if not token or not access_token_works(client_id, token):
            raise ValueError("Refreshed token failed validation")
        write_tokens({**cached, **refreshed})
    except Exception:
        # Refresh responses/exceptions can include credentials; do not forward them.
        raise FyersAuthenticationError(
            "Fyers automatic token refresh failed. Run python auth.py interactively on this server."
        ) from None
    print("Fyers access token refreshed and validated.", flush=True)
    return client_id, token


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required .env variable: {name}")
    return value


def token_path() -> Path:
    configured = os.getenv("token_file", "~/.fyers/token.json")
    return Path(os.path.expanduser(configured))


def read_tokens() -> dict[str, str]:
    """Read cached credentials without printing or exposing their values."""
    try:
        data = json.loads(token_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_tokens(tokens: dict[str, str]) -> None:
    """Atomically cache tokens outside the repository."""
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        # Windows does not consistently support Unix file modes.
        pass


def app_id_hash(client_id: str, secret_key: str) -> str:
    return hashlib.sha256(f"{client_id}:{secret_key}".encode("utf-8")).hexdigest()


def response_is_ok(response: Any) -> bool:
    return isinstance(response, dict) and (
        response.get("s") == "ok" or response.get("code") in {200, 201}
    )


def extract_auth_code(url: str, expected_state: str) -> str | None:
    """Extract and validate auth_code from a Fyers redirect URL."""
    parsed = urlsplit(url)
    values: dict[str, list[str]] = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in parse_qs(parsed.fragment, keep_blank_values=True).items():
        values.setdefault(key, value)

    error = values.get("error", [""])[0]
    if error:
        raise RuntimeError("Fyers login failed; retry authentication")

    returned_state = values.get("state", [None])[0]
    if returned_state and returned_state != expected_state:
        raise RuntimeError("Fyers redirect state does not match the .env state")

    return (values.get("auth_code") or values.get("code") or [None])[0]


def manual_auth_code(login_url: str, expected_state: str) -> str:
    """Print the login URL and accept a redirect URL/code in a headless shell."""
    print("Open this Fyers login URL in a browser on your local machine:")
    print(login_url)
    try:
        with warnings.catch_warnings():
            # getpass otherwise falls back to echoed input when no TTY exists.
            warnings.simplefilter('error', getpass.GetPassWarning)
            entered = getpass.getpass("Paste the full redirect URL or auth_code here (hidden): ").strip()
    except getpass.GetPassWarning:
        raise RuntimeError("Hidden credential input requires an interactive terminal") from None
    if not entered:
        raise RuntimeError("No auth_code or redirect URL was provided")

    candidate_url = entered
    if "auth_code=" in entered and "://" not in entered:
        candidate_url = f"https://localhost/?{entered}"
    return extract_auth_code(candidate_url, expected_state) or entered


def browser_auth_code(login_url: str, expected_state: str) -> str:
    """Use Chromium or a manual paste flow to obtain auth_code."""
    timeout = int(os.getenv("auth_timeout_seconds", "300"))
    headless = os.getenv("browser_headless", "false").lower() == "true"
    if headless or (os.name != "nt" and not os.getenv("DISPLAY")):
        return manual_auth_code(login_url, expected_state)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            try:
                page.goto(login_url, wait_until="domcontentloaded")
            except PlaywrightError:
                # A registered redirect URI may not serve a page. The URL can
                # still contain the auth_code after the redirect.
                pass

            print("Complete Fyers login/OTP in the browser window.")
            print(f"Waiting up to {timeout} seconds for the redirect...")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                code = extract_auth_code(page.url, expected_state)
                if code:
                    return code
                page.wait_for_timeout(1000)
            raise TimeoutError("Timed out waiting for the Fyers auth redirect")
        finally:
            browser.close()


def build_client(client_id: str, access_token: str) -> Any:
    return fyersModel.FyersModel(
        token=access_token,
        is_async=False,
        client_id=client_id,
        log_path="",
    )


def access_token_works(client_id: str, access_token: str) -> bool:
    """Validate the cached token with a lightweight profile request."""
    try:
        return response_is_ok(build_client(client_id, access_token).get_profile())
    except Exception:
        return False


def exchange_auth_code(app_session: Any, auth_code: str) -> dict[str, str]:
    try:
        app_session.set_token(auth_code)
        response = app_session.generate_token()
    except Exception:
        raise RuntimeError("Fyers authorization-code exchange failed") from None
    if not isinstance(response, dict) or (not response_is_ok(response) and "access_token" not in response):
        raise RuntimeError("Fyers authorization-code exchange failed")
    tokens = {
        key: str(response[key])
        for key in ("access_token", "refresh_token")
        if response.get(key)
    }
    if "access_token" not in tokens:
        raise RuntimeError("Fyers authorization response did not contain access_token")
    return tokens


def refresh_access_token(
    client_id: str, secret_key: str, refresh_token: str, pin: str
) -> dict[str, str]:
    """Use Fyers' refresh endpoint and retain the existing refresh token."""
    response = requests.post(
        REFRESH_ENDPOINT,
        json={
            "grant_type": "refresh_token",
            "appIdHash": app_id_hash(client_id, secret_key),
            "refresh_token": refresh_token,
            "pin": pin,
        },
        headers={"Content-Type": "application/json", "User-Agent": "fyers-auth-mvp/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not response_is_ok(data) and "access_token" not in data:
        raise RuntimeError(f"Fyers refresh failed: {data}")
    if not data.get("access_token"):
        raise RuntimeError("Fyers refresh response did not contain access_token")
    return {
        "access_token": str(data["access_token"]),
        "refresh_token": str(data.get("refresh_token", refresh_token)),
    }


def main() -> Any:
    redirect_uri = required_env("redirect_uri")
    client_id = required_env("client_id")
    secret_key = required_env("secret_key")
    grant_type = required_env("grant_type")
    response_type = required_env("response_type")
    state = required_env("state")

    app_session = fyersModel.SessionModel(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        state=state,
        secret_key=secret_key,
        grant_type=grant_type,
    )

    cached = read_tokens()
    access_token = os.getenv("FYERS_ACCESS_TOKEN") or cached.get("access_token")
    refresh_token = os.getenv("refresh_token") or cached.get("refresh_token")

    if access_token:
        if access_token_works(client_id, access_token):
            print("Using cached access token.")
        else:
            access_token = None

    if not access_token and refresh_token and os.getenv("pin"):
        try:
            refreshed = refresh_access_token(client_id, secret_key, refresh_token, required_env("pin"))
            cached.update(refreshed)
            write_tokens(cached)
            access_token = refreshed["access_token"]
            print("Access token refreshed successfully.")
        except Exception:
            print("Refresh unavailable; starting browser login")
            access_token = None

    if not access_token:
        auth_code = os.getenv("auth_code") or browser_auth_code(
            app_session.generate_authcode(), state
        )
        tokens = exchange_auth_code(app_session, auth_code)
        cached.update(tokens)
        write_tokens(cached)
        access_token = tokens["access_token"]
        print("Access and refresh tokens generated and cached successfully.")

    fyers = build_client(client_id, access_token)
    try:
        profile = fyers.get_profile()
    except Exception:
        raise RuntimeError("Fyers profile check failed") from None
    if not response_is_ok(profile):
        raise RuntimeError("Fyers profile check failed")
    print("Fyers authentication is ready.")
    return fyers


if __name__ == "__main__":
    main()
