"""BYO-credential OAuth for Gmail (PRD §7, M4 gate 1).

There is **no shared client id in this tree, and there never will be one.** A
shared client would make jobd-ai a data processor for everyone who installs it,
which is the hosted-service posture PRD §4 permanently rules out. The cost is
setup friction, which PRD §10 accepts explicitly; this wizard is the mitigation
that friction was traded for, so it ships in M4 rather than "later".

Scope was `gmail.readonly` alone through M4-M8 on purpose — this module's own
comment used to say so: requesting write access before anything writes it is
an unused scope on a live token, and an unused write scope is exactly the
thing an attacker inherits. `gmail.compose` (draft + send, not `gmail.modify`'s
full read/write/delete) is added now that something does write: the outbound
reply feature (SECURITY.md §4, `ports/sender.py`). `gmail.compose` over
`gmail.send` alone because `Sender.draft()` needs `drafts().create`, which
`gmail.send` does not grant.

Expanding SCOPES does not retroactively grant anything to an already-issued
token — Google authorizes what was actually consented to, not what this list
says. A token minted under the old readonly-only SCOPES keeps working for
reads (`Credentials.from_authorized_user_info` only tags scopes locally) and
gets a real 403 from Google the first time `drafts().create` is called on it.
Re-running `jobd auth gmail` is what actually grants the new scope — every
existing user has to do that once before sending anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2.rfc6749.errors import MismatchingStateError

from jobd.adapters.secrets import TokenStore

#: See the module docstring for why `compose` and not `send` or `modify`.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

TOKEN_PREFIX = "gmail"


class AuthError(RuntimeError):
    """Raised when a credential is missing, malformed, or unusable."""


def token_key(account: str) -> str:
    """Secret-store key for one account's token."""
    return f"{TOKEN_PREFIX}:{account}"


def load_credentials(account: str, store: TokenStore) -> Credentials:
    """Return usable credentials for an account, refreshing if needed.

    Raises:
        AuthError: If nothing is stored, or the refresh token has been revoked.
            Both are the same instruction to the user — run the wizard — so they
            are the same exception with different text.
    """
    payload = store.load(token_key(account))
    if payload is None:
        raise AuthError(
            f"No Gmail credential for {account!r}. Run: jobd auth gmail --account "
            f"{account}"
        )

    try:
        # The token's *own* recorded scopes, not the current SCOPES constant.
        # Google's refresh endpoint rejects a refresh request whose scope set
        # differs from what the refresh_token was actually issued for
        # (`invalid_scope`) — an old readonly-only token asked to refresh
        # against a SCOPES list that has since grown a `compose` entry fails
        # outright, breaking ordinary ingestion for every already-consented
        # account the moment SCOPES changes, not just the new send path.
        # Falls back to SCOPES only for a payload old enough to predate this
        # field existing at all.
        creds: Credentials = Credentials.from_authorized_user_info(
            payload, payload.get("scopes") or SCOPES
        )
    except ValueError as exc:
        # google-auth rejects a payload with no refresh_token here, before any
        # check of ours can run. Without this the user gets a library traceback
        # for a situation with a one-line fix.
        raise AuthError(
            f"Stored credential for {account!r} is unusable ({exc}). Re-run: "
            f"jobd auth gmail"
        ) from exc

    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise AuthError(
            f"Credential for {account!r} has expired and cannot refresh. Re-run "
            "the wizard."
        )
    try:
        creds.refresh(Request())
    except Exception as exc:  # google raises several unrelated types here
        raise AuthError(
            f"Refresh failed for {account!r} ({exc}). The grant was probably "
            "revoked in your Google account settings. Re-run the wizard."
        ) from exc
    # Refreshing mints a new access token; persisting it saves a round trip on
    # every subsequent run and keeps the stored rotation in step with Google's.
    store.save(token_key(account), json.loads(creds.to_json()))
    return creds


def validate_client_secret(path: Path) -> dict[str, Any]:
    """Check a client-secret file and return its parsed contents.

    Separate from :func:`run_wizard` so it can be run — and tested — without
    starting a callback server and a browser dance. Every failure below is one
    somebody hits on their first attempt, so each says what to do rather than
    what went wrong.

    Raises:
        AuthError: With an instruction, for every rejection.
    """
    if not path.is_file():
        raise AuthError(
            f"No client secret at {path}. See docs/gmail-setup.md — you create "
            "the OAuth client; jobd-ai ships none."
        )

    try:
        blob: dict[str, Any] = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AuthError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(blob, dict) or not ({"installed", "web"} & blob.keys()):
        raise AuthError(
            f"{path} does not look like an OAuth client secret. Download the "
            "*Desktop app* client JSON from Google Cloud Console → APIs & "
            "Services → Credentials."
        )
    if "web" in blob:
        raise AuthError(
            "That is a *Web application* client. Create a **Desktop app** client "
            "instead — a web client cannot use the loopback redirect this flow "
            "needs."
        )

    node = blob["installed"]
    missing = sorted({"client_id", "client_secret", "token_uri"} - set(node))
    if missing:
        raise AuthError(f"{path} is missing {', '.join(missing)}. Re-download it.")
    return blob


def extract_code(pasted: str, *, expected_state: str | None = None) -> str:
    """Pull the authorization code out of whatever the user pasted.

    Accepts the whole redirect URL — which is what people actually copy, since
    the browser is showing it and the page failed to load — or a bare code.

    Pure and separately tested, because the alternative is testing it through a
    consent screen.

    Raises:
        AuthError: If Google reported an error, the state does not match, or
            there is no code in there at all.
    """
    text = pasted.strip().strip("'\"")
    if not text:
        raise AuthError("Nothing pasted. Run the wizard again.")

    if "://" not in text and "code=" not in text:
        # A bare code. Google's are long and contain '/', so no further
        # validation here would catch a typo that fetch_token will not.
        return text

    query = parse_qs(urlparse(text).query if "://" in text else text.lstrip("?"))

    if "error" in query:
        error = query["error"][0]
        if error == "access_denied":
            raise AuthError(
                "Google returned access_denied. Either you declined, or your "
                "address is not on the OAuth consent screen's Test users list "
                "— see docs/gmail-setup.md §3."
            )
        raise AuthError(f"Google returned an error instead of a code: {error}")

    # State is the CSRF defence, and in this flow the user is the transport, so
    # it is the only thing standing between a pasted URL and a code minted by
    # someone else's consent. Checked when we know what to expect.
    if expected_state is not None:
        got = query.get("state", [None])[0]
        if got != expected_state:
            raise AuthError(
                "That URL is from a different consent attempt (state mismatch). "
                "Start over and use the URL this run printed."
            )

    codes = query.get("code")
    if not codes or not codes[0]:
        raise AuthError(
            "No `code` parameter in that URL. Copy the whole address bar from "
            "the page that failed to load, including everything after the `?`."
        )
    return codes[0]


def _consent_manual(
    flow: InstalledAppFlow,
    *,
    port: int,
    echo: Any,
    prompt: Callable[[str], str],
) -> Credentials:
    """Consent with no local server: the user carries the code back by hand.

    The redirect still points at loopback — it has to, because a Desktop client
    may register nothing else — but nothing is listening, so the browser lands
    on a connection-refused page with the code sitting in the address bar. That
    dead page *is* the handoff.

    This exists because the callback is the part that breaks: it needs a
    forwarded port, and over SSH, in a plain `docker run`, or on a remote box
    with no port forwarding there is nowhere for it to land. The obvious
    alternatives are both gone — Google disabled the OOB redirect
    (`urn:ietf:wg:oauth:2.0:oob`) in October 2022, taking `run_console()` with
    it, and the device-code flow's scope allowlist does not include Gmail.

    PKCE still applies: `authorization_url` generates the verifier and
    `fetch_token` sends it, so the pasted code is useless to anyone who
    intercepts it without this process's memory.
    """
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")

    echo("\nOpen this URL in any browser, on any machine:\n")
    echo(auth_url)
    echo(
        "\nAfter you approve, the browser will fail to load a localhost page. "
        "\nThat is expected — nothing is listening. Copy the URL out of the "
        "address bar\nand paste it here (the whole thing).\n"
    )
    code = extract_code(prompt("Pasted URL or code: "), expected_state=state)

    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # oauthlib raises a family of unrelated errors
        raise AuthError(
            f"Google rejected that code ({exc}). Codes are single-use and expire "
            "within minutes — run the wizard again and paste the new one."
        ) from exc
    return flow.credentials  # type: ignore[no-any-return]


def run_wizard(
    client_secret_path: Path,
    store: TokenStore,
    *,
    port: int = 8765,
    open_browser: bool = False,
    manual: bool = False,
    echo: Any = print,
    prompt: Callable[[str], str] = input,
) -> str:
    """Run the consent flow and store the resulting token. Returns the account.

    The account is not asked for — it is read back from the token, so a typo
    cannot store a credential under the wrong address.

    Args:
        client_secret_path: The JSON the user downloaded from their own Google
            Cloud project.
        store: Where the token lands.
        port: Local callback port. Fixed rather than ephemeral because in a
            container the browser runs on the host, and a forwarded port has to
            be predictable to be forwarded at all. Under `manual` nothing binds
            it, but it still has to match the redirect Google sees.
        open_browser: False by default. In a container there is no browser to
            open, and a silently failing launch looks like a hang.
        manual: Skip the callback server and have the user paste the code back.
            For anywhere the port cannot be forwarded.
        echo: Injected for tests and for click's echo.
        prompt: Injected so the manual path is testable without a terminal.
    """
    validate_client_secret(client_secret_path)

    if not store.is_os_keyring:
        echo(
            "Note: no OS keyring on this machine. The token will be written to "
            f"{store.backend}. Outside the repo and outside Postgres, but weaker "
            "than a keychain — see SECURITY.md §3."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    if manual:
        creds = _consent_manual(flow, port=port, echo=echo, prompt=prompt)
    else:
        try:
            creds = flow.run_local_server(
                port=port,
                open_browser=open_browser,
                # Without these two, Google returns no refresh token on a second
                # consent, and the daemon silently stops working when the access
                # token expires an hour later.
                access_type="offline",
                prompt="consent",
            )
        except MismatchingStateError as exc:
            # Every run mints fresh state. Approving a consent URL left over
            # from an earlier attempt — the tab is still open, and after a 403
            # there is always an earlier attempt — delivers the old state to the
            # new server, and oauthlib ends the run with a raw CSRF traceback
            # that reads like a security incident. It is a stale browser tab.
            raise AuthError(
                "The browser completed a consent URL from an earlier run "
                "(state mismatch). Close any leftover consent tabs, re-run this "
                "command, and open only the URL it prints — a private window "
                "makes that unambiguous. If the port cannot be forwarded at "
                "all, use: jobd auth gmail --manual"
            ) from exc

    account = _address_for(creds)
    store.save(token_key(account), json.loads(creds.to_json()))
    return account


def _address_for(creds: Credentials) -> str:
    """Ask Gmail which mailbox this token is for."""
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    address = profile.get("emailAddress")
    if not address:
        raise AuthError("Gmail returned no email address for this token.")
    return str(address)
