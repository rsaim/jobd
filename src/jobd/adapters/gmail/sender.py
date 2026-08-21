"""Gmail as a `Sender` (ports/sender.py, PRD P5, I1).

Two Gmail API calls, one per `Sender` method, and nothing in between:
`draft()` is `users().drafts().create()`, `send()` is `users().drafts().send()`.
Splitting them the way the port already does means a drafted reply sits in
the account's own Drafts folder — visible, editable, deletable in Gmail
itself — for however long passes between "drafted" and "a human clicked
Send", not just inside jobd's own UI.

Threading is RFC headers (`In-Reply-To`/`References`) *plus* Gmail's
`threadId`, carried as the port's opaque `Draft.thread_ref`. The headers are
what threads the mail for every RFC-speaking client once it is sent; the
`threadId` is what makes the still-unsent draft appear inside its
conversation in Gmail's own UI — without it Gmail shows a reply draft as a
standalone message, which reads as "new email" rather than "reply". The
port stays channel-agnostic: `thread_ref` is a string the caller read off
the message being replied to, and this adapter is the only thing that knows
it spells `threadId`.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from googleapiclient.errors import HttpError

from jobd.adapters.gmail.auth import load_credentials
from jobd.adapters.secrets import TokenStore
from jobd.ports.sender import Approval, Draft


class SendNotAuthorized(RuntimeError):
    """The stored token predates `gmail.compose` being requested.

    Distinct from `auth.AuthError` (no credential at all) — this is "you have
    a working read credential, it just cannot draft or send", which needs a
    different instruction: re-run the wizard for the same account, don't set
    one up from scratch.
    """


def _default_service(account: str, store: TokenStore) -> Any:
    from googleapiclient.discovery import build

    creds = load_credentials(account, store)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailSender:
    """Drafts and sends through one Gmail account's own OAuth credential —
    the same `TokenStore`-backed credential `GmailSource` reads with, now
    also carrying `gmail.compose` (see `auth.py`'s module docstring)."""

    channel = "gmail"

    def __init__(self, store: TokenStore, service_factory: Any = _default_service) -> None:
        self._store = store
        self._factory = service_factory
        self._services: dict[str, Any] = {}

    def _service_for(self, account: str) -> Any:
        if account not in self._services:
            self._services[account] = self._factory(account, self._store)
        return self._services[account]

    def draft(self, draft: Draft) -> str:
        # stdlib EmailMessage over the legacy MIMEText: it is the modern
        # RFC 5322 implementation — correct header folding, address
        # encoding, and content transfer encoding all come from `email`'s
        # default policy instead of hand-rolling.
        message = EmailMessage()
        message["To"] = ", ".join(draft.to)
        if draft.cc:
            message["Cc"] = ", ".join(draft.cc)
        message["From"] = draft.account
        message["Subject"] = draft.subject
        if draft.in_reply_to:
            message["In-Reply-To"] = draft.in_reply_to
            message["References"] = draft.references or draft.in_reply_to
        message.set_content(draft.body)

        payload: dict[str, Any] = {
            "raw": base64.urlsafe_b64encode(message.as_bytes()).decode()
        }
        if draft.thread_ref:
            payload["threadId"] = draft.thread_ref
        try:
            result = self._service_for(draft.account).users().drafts().create(
                userId="me", body={"message": payload}
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 403:
                raise SendNotAuthorized(
                    f"{draft.account!r}'s stored Gmail credential does not have "
                    "send permission. Run: jobd auth gmail --account "
                    f"{draft.account} to grant it (gmail.compose)."
                ) from exc
            raise
        return str(result["id"])

    def send(self, account: str, draft_id: str, approval: Approval) -> str:
        if approval.draft_id != draft_id:
            raise ValueError(
                f"Approval is for draft {approval.draft_id!r}, not {draft_id!r} — refusing."
            )
        try:
            result = self._service_for(account).users().drafts().send(
                userId="me", body={"id": draft_id}
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 403:
                raise SendNotAuthorized(
                    f"{account!r}'s stored Gmail credential does not have send "
                    f"permission. Run: jobd auth gmail --account {account}"
                ) from exc
            raise
        return str(result["id"])
