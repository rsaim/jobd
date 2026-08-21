"""Gmail as a `MessageSource` (PRD P1).

Two modes, and the difference is the whole of M4 gate 3:

* **Full sync** — `messages.list` then one `messages.get` per message. Linear in
  mailbox size. Run once.
* **Incremental** — `history.list` from a stored history id. Linear in *new*
  messages, which is normally a handful. Run every time after that.

Both yield `RawMessage` with `format="raw"` payloads: the original RFC 5322
bytes, untouched. M4 moves bytes; M5 reads them. Storing Gmail's parsed JSON
instead would bake this build's idea of a message into the source of truth, and
I3 promises the opposite — that a better parser can re-derive the world.

`api_calls` counts requests. Gate 3 asks for the incremental run to be *verified
against the API call count*, which means the count has to be observable rather
than inferred from wall-clock.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from jobd.adapters.gmail.auth import load_credentials
from jobd.adapters.secrets import TokenStore
from jobd.domain.raw import RawMessage

#: Gmail caps `maxResults` at 500 for both list and history.
PAGE_SIZE = 500


class HistoryTooOld(RuntimeError):
    """Gmail expired the history id; only a full sync can recover.

    Gmail keeps history for about a week. A laptop closed over a holiday comes
    back to a 404 here, and the correct response is a full sync — which is safe
    because ingestion is idempotent (I2), just slower.
    """


def _default_service(account: str, store: TokenStore) -> Any:
    from googleapiclient.discovery import build

    creds = load_credentials(account, store)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailSource:
    """One Gmail adapter, serving every configured account.

    Args:
        store: Where OAuth tokens live.
        service_factory: ``(account, store) -> service``. Injected in tests; the
            default builds a real client. The seam is here rather than at the
            HTTP layer because the discovery client is what the code actually
            talks to, and mocking one layer below would test the mock.
    """

    name = "gmail"

    def __init__(
        self,
        store: TokenStore,
        service_factory: Any = _default_service,
        accounts: list[str] | None = None,
    ) -> None:
        self._store = store
        self._factory = service_factory
        self._accounts = accounts
        self._checkpoints: dict[str, str] = {}
        self._services: dict[str, Any] = {}
        self.api_calls = 0

    def _service_for(self, account: str) -> Any:
        """One discovery client per account, reused across calls.

        Day-batched ingest calls :meth:`list_ids` once and :meth:`get_one` many
        times per day-worker process; rebuilding credentials and the client on
        every message would be pure overhead within a single account's run.
        """
        if account not in self._services:
            self._services[account] = self._factory(account, self._store)
        return self._services[account]

    def accounts(self) -> list[str]:
        """Configured accounts.

        Explicit when given, otherwise whatever the secret store can enumerate —
        which on a real keychain is nothing, since `keyring` has no portable
        listing API. That is why the CLI takes `--account` rather than
        discovering.
        """
        if self._accounts is not None:
            return list(self._accounts)
        from jobd.adapters.gmail.auth import TOKEN_PREFIX

        prefix = f"{TOKEN_PREFIX}:"
        keys = self._store.accounts()
        return [k[len(prefix) :] for k in keys if k.startswith(prefix)]

    def checkpoint(self, account: str) -> str | None:
        """History id to resume from, learned during the last :meth:`fetch`."""
        return self._checkpoints.get(account)

    # ------------------------------------------------------------------ fetch

    def fetch(
        self,
        account: str,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> Iterator[RawMessage]:
        """Yield messages for one account.

        A cursor takes precedence over ``since``: a history id is exact, and a
        timestamp is a guess that re-walks everything after it.

        Yields lazily. A five-year backfill must not be assembled in memory, and
        the caller writes each message through to storage as it arrives so a
        crash halfway leaves the first half durably stored (I2 makes the re-run
        free).
        """
        service = self._factory(account, self._store)

        # Read the mailbox's current history id *before* fetching. Taking it
        # afterwards would silently skip anything that arrived mid-sync.
        profile = self._call(service.users().getProfile(userId="me"))
        head = str(profile["historyId"])

        if cursor:
            yield from self._incremental(service, account, cursor)
        else:
            yield from self._full(service, account, since)

        self._checkpoints[account] = head

    def _full(
        self, service: Any, account: str, since: datetime | None
    ) -> Iterator[RawMessage]:
        query = f"after:{int(since.timestamp())}" if since else None
        page: str | None = None
        while True:
            response = self._call(
                service.users().messages().list(
                    userId="me", q=query, pageToken=page, maxResults=PAGE_SIZE
                )
            )
            for stub in response.get("messages", []):
                yield self._get(service, account, stub["id"])
            page = response.get("nextPageToken")
            if not page:
                return

    def _incremental(
        self, service: Any, account: str, cursor: str
    ) -> Iterator[RawMessage]:
        page: str | None = None
        seen: set[str] = set()
        while True:
            try:
                response = self._call(
                    service.users().history().list(
                        userId="me",
                        startHistoryId=cursor,
                        historyTypes=["messageAdded"],
                        pageToken=page,
                        maxResults=PAGE_SIZE,
                    )
                )
            except Exception as exc:
                if _is_history_gone(exc):
                    raise HistoryTooOld(
                        f"Gmail no longer has history from {cursor} for {account}. "
                        "Re-run without --cursor for a full sync; ingestion is "
                        "idempotent, so nothing duplicates."
                    ) from exc
                raise

            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_id = added["message"]["id"]
                    # One message can appear in several history records — a
                    # label change after delivery, for instance. Fetching it
                    # twice would cost an API call to produce a byte-identical
                    # object.
                    if message_id in seen:
                        continue
                    seen.add(message_id)
                    yield self._get(service, account, message_id)

            page = response.get("nextPageToken")
            if not page:
                return

    # ------------------------------------------------------- day-batched path

    def list_ids(self, account: str, day: date) -> Iterator[str]:
        """Ids of every message that arrived on ``day`` (UTC).

        A list call is orders of magnitude cheaper than a get, which is the
        whole point of splitting listing from fetching: a resuming day-worker
        can learn what exists before paying for what it already has.

        Bounds are UTC epoch seconds, not Gmail's ``YYYY/MM/DD`` query syntax —
        the latter is interpreted in the mailbox's own timezone, which would
        make "day" mean something different from the UTC day
        ``keys.storage_key`` buckets by.
        """
        service = self._service_for(account)
        start_of_day = datetime(day.year, day.month, day.day, tzinfo=UTC)
        start = int(start_of_day.timestamp())
        end = int((start_of_day + timedelta(days=1)).timestamp())
        query = f"after:{start} before:{end}"
        page: str | None = None
        while True:
            response = self._call(
                service.users().messages().list(
                    userId="me", q=query, pageToken=page, maxResults=PAGE_SIZE
                )
            )
            for stub in response.get("messages", []):
                yield stub["id"]
            page = response.get("nextPageToken")
            if not page:
                return

    def get_one(self, account: str, message_id: str) -> RawMessage:
        """Fetch a single message by id. The expensive half of the day path."""
        return self._get(self._service_for(account), account, message_id)

    # ------------------------------------------------------ query-driven path

    def search_ids(self, account: str, query: str) -> Iterator[str]:
        """Ids of every message matching a Gmail search query.

        The seed-and-expand scraper's whole listing mechanism (scrape/
        queries.py builds the queries). Same list/get split as the day path,
        for the same reason — and yield is measured by counting these ids,
        never by ``resultCountEstimate``, which the experiment series found
        capped at a fixed value regardless of the real total.
        """
        service = self._service_for(account)
        page: str | None = None
        while True:
            response = self._call(
                service.users().messages().list(
                    userId="me", q=query, pageToken=page, maxResults=PAGE_SIZE
                )
            )
            for stub in response.get("messages", []):
                yield stub["id"]
            page = response.get("nextPageToken")
            if not page:
                return

    def thread_message_ids(self, account: str, thread_id: str) -> list[str]:
        """Every message id in one conversation.

        Gmail search cannot filter by thread id, so expansion resolves thread
        entities with ``threads.get`` instead — one call returns the whole
        chain, minimal format (ids only, no bodies billed).
        """
        service = self._service_for(account)
        response = self._call(
            service.users().threads().get(
                userId="me", id=thread_id, format="minimal"
            )
        )
        return [m["id"] for m in response.get("messages", [])]

    def _get(self, service: Any, account: str, message_id: str) -> RawMessage:
        """Fetch one message as its original bytes."""
        payload = self._call(
            service.users().messages().get(userId="me", id=message_id, format="raw")
        )
        metadata = {
            "thread_id": str(payload.get("threadId", "")),
            "labels": ",".join(payload.get("labelIds", [])),
        }
        # `internalDate` is Gmail's own timestamp for the message (roughly, when
        # it arrived) — epoch milliseconds, as a string. Stable across every
        # future fetch of the same message, unlike `fetched_at` below, which is
        # why it — and not `fetched_at` — is what the storage key's date
        # directory is built from (see keys.OCCURRED_AT_META_KEY).
        internal_ms = payload.get("internalDate")
        if internal_ms:
            occurred = datetime.fromtimestamp(int(internal_ms) / 1000, tz=UTC)
            metadata["occurred_at"] = occurred.isoformat()
        return RawMessage(
            source=self.name,
            external_id=message_id,
            account=account,
            fetched_at=datetime.now(UTC),
            payload=base64.urlsafe_b64decode(payload["raw"]),
            metadata=metadata,
        )

    def _call(self, request: Any) -> Any:
        """Execute one API request, counting it."""
        self.api_calls += 1
        result: Any = request.execute()
        return result


def _is_history_gone(exc: Exception) -> bool:
    """True for the 404 Gmail returns when a history id has aged out."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 404
