"""Backup — snapshot and restore of the derived store (PRD §6)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One restorable point in time."""

    id: str
    created_at: datetime
    size_bytes: int


@runtime_checkable
class Backup(Protocol):
    """Snapshots of Postgres — the *derived* store, not the raw archive.

    Worth stating why this port is not load-bearing: Postgres is rebuildable
    from raw storage by definition (I3), so a lost snapshot costs re-derivation
    time, never data. Backup is a convenience over ``jobd rebuild``, and if the
    two ever disagree, raw storage wins.
    """

    def create(self, label: str | None = None) -> Snapshot:
        """Take a snapshot of the current derived state."""
        ...

    def restore(self, snapshot_id: str) -> None:
        """Replace derived state with a snapshot. Raw storage is untouched."""
        ...

    def list(self) -> Iterator[Snapshot]:
        """Yield known snapshots, newest first."""
        ...
