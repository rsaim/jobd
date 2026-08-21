"""Filesystem implementation of the `Storage` port.

The first adapter in the tree. It exists in M3 rather than M4 because the schema
milestone has to prove the key layout round-trips, and proving that against S3
would mean network calls in a milestone whose gate is about Postgres.

It is not a test double. The `local` profile can legitimately run on it — a user
who does not want an S3 bucket loses durability and nothing else. The S3 adapter
in M4 implements the same port with the same key function, so an archive written
by one is readable by the other.

Write-once, like the bucket: see :meth:`FilesystemStorage.put`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from jobd.domain.keys import storage_key
from jobd.domain.raw import RawMessage, decode, encode


class FilesystemStorage:
    """Raw storage rooted at a local directory.

    Args:
        root: Directory to store under. Created if absent.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Resolve a key to a path, refusing anything that escapes the root.

        Keys come from :func:`storage_key` today, but ``get``/``exists`` are
        public and a key is a string. A traversal check here is four lines; a
        missing one is a file-read primitive.
        """
        candidate = (self._root / key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return candidate

    def key_for(self, message: RawMessage) -> str:
        """The content-hash key this message stores under."""
        return storage_key(message)

    def put(self, message: RawMessage) -> str:
        """Persist a message, returning its key.

        **Write-once.** If the key exists, this returns without touching the
        file. Re-fetching the same message produces an envelope whose
        ``fetched_at`` differs, so overwriting would rewrite every object on
        every run — against S3 that means a new version per object per run,
        which is both a cost and a lie about what changed. First write wins, and
        ``fetched_at`` therefore records first retrieval.

        The write itself goes to a temp file and is renamed into place, so a
        crash mid-write cannot leave a truncated envelope that later reads as
        corrupt archive data.
        """
        key = self.key_for(message)
        path = self._path(key)
        if path.exists():
            return key

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encode(message))
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return key

    def get(self, key: str) -> RawMessage:
        """Read one message back.

        Raises:
            KeyError: If the key is absent.
        """
        path = self._path(key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(key) from exc
        return decode(raw)

    def exists(self, key: str) -> bool:
        """True if the key is present, without reading the payload."""
        return self._path(key).is_file()

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield stored keys under a prefix, sorted.

        Sorted so ``jobd rebuild`` is deterministic: two rebuilds of the same
        archive process messages in the same order, which makes their outputs
        comparable when a prompt change is under evaluation (I3).
        """
        for path in sorted(self._root.rglob("*.jsonl")):
            key = path.relative_to(self._root).as_posix()
            if key.startswith(prefix):
                yield key
