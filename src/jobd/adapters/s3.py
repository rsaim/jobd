"""S3 implementation of the `Storage` port — the durable source of truth.

Same key function and same envelope as `FilesystemStorage`, so an archive
written by one is readable by the other. That is not a nicety: it is what lets a
user start on the filesystem and move to a bucket without a migration, and it is
what makes the contract tests in tests/test_storage_contract.py meaningful.

Runs on the scoped `jobd-runtime` credential (SECURITY.md §2), which grants
Get/Put/List on one bucket and **no `s3:DeleteObject`**. Nothing here tries to
delete, so the adapter and the credential agree — a port wider than the
credential would be a lie discovered at runtime, in production, on a Sunday.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
from botocore.exceptions import ClientError

from jobd.domain.keys import storage_key
from jobd.domain.raw import RawMessage, decode, encode

#: Returned by S3 when a conditional write loses. Not an error condition here —
#: it means somebody else already stored the identical object.
_ALREADY_THERE = {"PreconditionFailed", "ConditionalRequestConflict"}
_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3Storage:
    """Raw storage in one S3 bucket.

    Args:
        bucket: Bucket name. One bucket, always — the IAM policy names it.
        client: A boto3 S3 client, injected for tests. Defaults to one from
            the ambient session, so the profile in ``~/.aws`` decides which
            credential runs.
    """

    def __init__(self, bucket: str, client: Any = None) -> None:
        self._bucket = bucket
        self._s3: Any = client if client is not None else boto3.client("s3")

    @property
    def bucket(self) -> str:
        return self._bucket

    def key_for(self, message: RawMessage) -> str:
        """The content-hash key this message stores under."""
        return storage_key(message)

    def put(self, message: RawMessage) -> str:
        """Persist a message, returning its key. Write-once.

        Uses a conditional write (``IfNoneMatch: *``) rather than
        read-then-write. A check followed by a put has a race between them, and
        the loser overwrites — which against a versioned bucket means an extra
        version of an object whose content did not change. The condition makes
        "first write wins" a property of the request instead of a hope.
        """
        key = self.key_for(message)
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=encode(message),
                ContentType="application/x-ndjson",
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in _ALREADY_THERE:
                raise
            # Already stored. `fetched_at` therefore records first retrieval.
        return key

    def get(self, key: str) -> RawMessage:
        """Read one message back.

        Raises:
            KeyError: If the key is absent.
        """
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise KeyError(key) from exc
            raise
        body: bytes = response["Body"].read()
        return decode(body)

    def exists(self, key: str) -> bool:
        """True if the key is present, without transferring the payload."""
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in _MISSING:
                return False
            raise
        return True

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield stored keys under a prefix, paginated.

        S3 returns keys in lexicographic order, which is the same order
        `FilesystemStorage` sorts into — so `jobd rebuild` walks an archive in
        the same order whichever storage backs it, and two rebuilds stay
        comparable when a prompt change is under evaluation (I3).
        """
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield str(item["Key"])
