"""MessageSource — where raw messages come from (PRD P1)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from jobd.domain.raw import RawMessage


@runtime_checkable
class MessageSource(Protocol):
    """A channel jobd can pull messages from.

    v1 implements Gmail and the LinkedIn archive. IMAP and Unipile are v1.5.

    Implementations must be **idempotent** (I2): calling ``fetch`` twice over
    the same window yields messages that hash identically, so the write-through
    to S3 is a no-op the second time. Nothing downstream deduplicates for you.
    """

    @property
    def name(self) -> str:
        """Stable channel identifier, written into ``RawMessage.source``."""
        ...

    def accounts(self) -> list[str]:
        """The user's configured accounts on this channel. May be several."""
        ...

    def fetch(
        self,
        account: str,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> Iterator[RawMessage]:
        """Yield messages for one account.

        Args:
            account: One value from :meth:`accounts`.
            since: Lower bound on arrival. ``None`` means full history.
            cursor: Opaque incremental token from a prior run (Gmail's history
                id, for instance). Takes precedence over ``since`` when both are
                given — a cursor is exact and a timestamp is a guess.

        Yields lazily: a five-year backfill must not be assembled in memory.
        """
        ...

    def checkpoint(self, account: str) -> str | None:
        """The cursor to resume from next run, or ``None`` if not resumable."""
        ...
