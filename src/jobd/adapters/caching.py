"""A local disk cache in front of another `Storage` — usually S3.

Not a new format. The cache *is* a `FilesystemStorage` rooted at some local
directory, using the identical content-hash key layout as the backing store,
so a cached object round-trips through the exact envelope encode/decode this
tree already trusts (see filesystem.py's own docstring on why that matters).

The backing store stays the source of truth (I3) — this is purely a speedup:
every header/metadata re-read this project does (fanout investigation, sender
ranking, reclassify) was paying a network round trip for bytes already fetched
once. The cache is droppable and re-populatable from the backing store at any
time; nothing here is the only copy of anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from jobd.adapters.filesystem import FilesystemStorage
from jobd.domain.raw import RawMessage
from jobd.ports import Storage


class CachingStorage:
    """Wraps a backing `Storage`, serving `get` from a local disk cache first."""

    def __init__(self, backing: Storage, cache_dir: Path | str) -> None:
        self._backing = backing
        self._cache = FilesystemStorage(cache_dir)

    def key_for(self, message: RawMessage) -> str:
        return self._backing.key_for(message)

    def put(self, message: RawMessage) -> str:
        """Write-through: the backing store is written first (it is the
        source of truth), then the cache, under the same key."""
        key = self._backing.put(message)
        self._cache.put(message)
        return key

    def exists(self, key: str) -> bool:
        """Backing store only — a cache miss must not be reported as absent."""
        return self._backing.exists(key)

    def get(self, key: str) -> RawMessage:
        """Cache first, backing store on a miss — populating the cache so the
        next read of this key never leaves the machine."""
        try:
            return self._cache.get(key)
        except KeyError:
            pass
        message = self._backing.get(key)
        self._cache.put(message)
        return message

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Backing store only — the cache is a subset by construction (only
        ever grows via `get`/`put`), never a complete listing on its own."""
        return self._backing.iter_keys(prefix)
