"""The raw envelope, and its on-disk form.

Raw messages are the source of truth (PRD §6, invariant I3). Everything else is
derived and rebuildable, which is why this type is deliberately dumb: an opaque
payload plus enough provenance to fetch it again and to key it in S3.

Interpretation of `payload` belongs to M5. The Postgres record belongs to M3.

:func:`encode` and :func:`decode` are here rather than in an adapter because the
envelope format *is* the source of truth's format. An adapter that invented its
own would make the archive readable only by that adapter, which is the opposite
of what I3 promises.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

#: Written into every envelope. A reader that meets a version it does not know
#: should refuse rather than guess — a misparsed archive is worse than no
#: archive, because it looks like it worked.
ENVELOPE_VERSION = 1


@dataclass(frozen=True, slots=True)
class RawMessage:
    """One immutable message as it arrived, before any interpretation.

    Attributes:
        source: Channel identifier, e.g. ``"gmail"`` or ``"linkedin-archive"``.
        external_id: The source's own stable id. Used for provenance, *not* as
            the storage key — keys are content hashes (P1), so a source that
            renumbers cannot cause a duplicate write.
        account: Which of the user's accounts this arrived on. Multi-account is
            a P1 requirement, so it is on the envelope from the start.
        fetched_at: When jobd retrieved it. Not when it was sent — that lives in
            the payload and is the extractor's problem.
        payload: The message exactly as received. Bytes, not str: mail is not
            reliably valid UTF-8 and re-encoding it would break the hash.
        metadata: Source-supplied labels kept verbatim. Read-only.
    """

    source: str
    external_id: str
    account: str
    fetched_at: datetime
    payload: bytes
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class EnvelopeError(ValueError):
    """Raised when stored bytes are not an envelope this version understands."""


def encode(message: RawMessage) -> bytes:
    """Serialise one message to a single JSONL line.

    Payload is base64 because mail is arbitrary bytes and JSON strings are not.
    Round-tripping it through a text codec would corrupt attachments and change
    the content hash, which would in turn duplicate the object on re-ingest.

    Keys are sorted and separators fixed so the same message always produces
    byte-identical output. Not required by the hash — which is computed from the
    payload, not from this — but it makes ``diff`` on two archives meaningful.
    """
    body = {
        "v": ENVELOPE_VERSION,
        "source": message.source,
        "external_id": message.external_id,
        "account": message.account,
        "fetched_at": message.fetched_at.astimezone(UTC).isoformat(),
        "payload_b64": base64.b64encode(message.payload).decode("ascii"),
        "metadata": dict(message.metadata),
    }
    line = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (line + "\n").encode("utf-8")


def decode(raw: bytes) -> RawMessage:
    """Parse one JSONL line back into a message.

    Raises:
        EnvelopeError: On malformed JSON, an unknown envelope version, or a
            missing field. Never returns a partially-populated message — a
            half-read envelope entering the rebuild is a silent data bug.
    """
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"not valid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise EnvelopeError(f"envelope must be an object, got {type(body).__name__}")

    version = body.get("v")
    if version != ENVELOPE_VERSION:
        raise EnvelopeError(
            f"envelope version {version!r} is not readable by this build "
            f"(expected {ENVELOPE_VERSION})"
        )

    try:
        return RawMessage(
            source=body["source"],
            external_id=body["external_id"],
            account=body["account"],
            fetched_at=datetime.fromisoformat(body["fetched_at"]),
            payload=base64.b64decode(body["payload_b64"], validate=True),
            metadata=MappingProxyType(dict(body.get("metadata", {}))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvelopeError(f"malformed envelope: {exc}") from exc
