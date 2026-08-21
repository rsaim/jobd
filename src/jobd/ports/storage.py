"""Storage — durable raw storage, the source of truth (PRD P1, I3)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from jobd.domain.raw import RawMessage


@runtime_checkable
class Storage(Protocol):
    """Write-through raw storage. S3 in v1; a filesystem adapter for tests.

    Content-hash keyed (P1), which is what makes ingestion idempotent by
    construction rather than by a dedupe pass: the same bytes produce the same
    key, so a re-run overwrites itself.

    **No delete method exists, deliberately.** Raw messages are immutable once
    written; retention is the bucket's lifecycle policy. The runtime IAM
    credential has no ``s3:DeleteObject`` either, so this port matches what the
    credential can actually do (SECURITY.md §2).
    """

    def key_for(self, message: RawMessage) -> str:
        """The content-hash key this message stores under.

        Must be stable across processes and runs — M3 gate 4 tests exactly that.
        """
        ...

    def put(self, message: RawMessage) -> str:
        """Persist a message, returning its key. Idempotent for equal bytes."""
        ...

    def get(self, key: str) -> RawMessage:
        """Read one message back. Raises ``KeyError`` if absent."""
        ...

    def exists(self, key: str) -> bool:
        """True if the key is present, without transferring the payload."""
        ...

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield stored keys. This is what ``jobd rebuild`` walks (I3)."""
        ...
