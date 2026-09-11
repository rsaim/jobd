"""A hand-entered stage must land in the generation the dashboard reads.

Offline conversations are the one write path that creates a stage event no
model proposed. The failure mode worth testing is not a crash -- it is a
silent success: reads keep only rows whose `derived_at` matches the
application's (`latest_batch`, `dashboard._CURRENT_BATCH`), so a manual row
left at NULL on a derived application is written, reported as saved, and then
filtered out of every view. These tests pin the stamp.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from jobd.domain.record import latest_batch
from jobd.services.conversations import KINDS, STAGES, delete, record

COMPANY = uuid4()
APP = uuid4()
BATCH = datetime(2026, 9, 3, 16, 27, tzinfo=UTC)
WHEN = datetime(2025, 10, 15, 12, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return []


class FakeConn:
    """Records every statement and answers the two SELECTs `record` makes."""

    def __init__(self, batch: datetime | None = BATCH, app: UUID | None = APP) -> None:
        self.statements: list[tuple[str, Any]] = []
        self._batch = batch
        self._app = app
        self.ids = {"stage_event": uuid4(), "conversation": uuid4()}

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self.statements.append((" ".join(sql.split()), params))
        low = sql.lower()
        if "select derived_at from application" in low:
            return FakeCursor((self._batch,))
        if "from application a" in low and "left join message" in low:
            return FakeCursor((self._app,) if self._app else None)
        if "insert into stage_event" in low:
            return FakeCursor((self.ids["stage_event"],))
        if "insert into conversation" in low:
            return FakeCursor((self.ids["conversation"],))
        if "delete from conversation" in low:
            return FakeCursor((self.ids["stage_event"],))
        return FakeCursor(None)

    def sql_containing(self, needle: str) -> list[tuple[str, Any]]:
        return [s for s in self.statements if needle in s[0].lower()]


def test_manual_stage_joins_the_applications_current_batch() -> None:
    """The whole point: stamp it, or the row is invisible on every view."""
    conn = FakeConn(batch=BATCH)
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="offer")
    (_, params), = conn.sql_containing("insert into stage_event")
    assert params[-1] == BATCH


def test_undrived_application_keeps_a_null_stamp() -> None:
    """An application no derivation reached reads NULL-for-NULL; match that."""
    conn = FakeConn(batch=None)
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="offer")
    (_, params), = conn.sql_containing("insert into stage_event")
    assert params[-1] is None


def test_stamped_row_survives_the_reader() -> None:
    """End-to-end on the rule that motivated the stamp."""

    class Row:
        def __init__(self, batch: datetime | None, stage: str) -> None:
            self.application_id = APP
            self.derived_at = batch
            self.stage = stage

    derived = Row(BATCH, "declined")
    manual = Row(BATCH, "offer")
    stale = Row(None, "onsite")
    kept = latest_batch([derived, manual, stale])
    assert manual in kept and stale not in kept


def test_unstamped_manual_row_would_vanish() -> None:
    """Documents the bug the stamp prevents, so nobody 'simplifies' it away."""

    class Row:
        def __init__(self, batch: datetime | None, stage: str) -> None:
            self.application_id = APP
            self.derived_at = batch
            self.stage = stage

    derived = Row(BATCH, "declined")
    unstamped = Row(None, "offer")
    assert unstamped not in latest_batch([derived, unstamped])


def test_final_stage_settles_the_application() -> None:
    conn = FakeConn()
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="declined")
    assert conn.sql_containing("update application set outcome")


def test_offer_does_not_overwrite_how_it_ended() -> None:
    """The bug this caught: recording a verbal offer on a chain that ends in a
    decline flipped `outcome` from declined to offer, erasing the ending. An
    offer is a milestone; it reaches the funnel via its stage event."""
    conn = FakeConn()
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="offer")
    assert conn.sql_containing("insert into stage_event")
    assert not conn.sql_containing("update application set outcome")


def test_outcome_only_moves_forward_in_time() -> None:
    """A decline typed in for last year must not unseat a later acceptance."""
    conn = FakeConn()
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="declined")
    (sql, _), = conn.sql_containing("update application set outcome")
    assert "ended_at is null or ended_at <=" in sql.lower()


def test_non_outcome_stage_leaves_outcome_alone() -> None:
    conn = FakeConn()
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="onsite")
    assert not conn.sql_containing("update application set outcome")


def test_conversation_without_a_stage_writes_no_stage_event() -> None:
    """Most chats are context, not a verdict."""
    conn = FakeConn()
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", notes="chat")
    assert not conn.sql_containing("insert into stage_event")


def test_stage_needs_an_application_to_attach_to() -> None:
    """A first-contact call can precede any application; record it anyway."""
    conn = FakeConn(app=None)
    record(conn, company_id=COMPANY, occurred_at=WHEN, kind="phone", stage="offer")
    assert not conn.sql_containing("insert into stage_event")
    assert conn.sql_containing("insert into conversation")


def test_deleting_takes_the_asserted_stage_with_it() -> None:
    conn = FakeConn()
    assert delete(conn, uuid4()) is True
    assert conn.sql_containing("delete from stage_event")


@pytest.mark.parametrize("bad", ["telepathy", "", "PHONE"])
def test_unknown_kind_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        record(FakeConn(), company_id=COMPANY, occurred_at=WHEN, kind=bad)


@pytest.mark.parametrize("bad", ["hired", "ghosted", "Offer"])
def test_unknown_stage_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        record(
            FakeConn(), company_id=COMPANY, occurred_at=WHEN, kind="phone", stage=bad
        )


def test_stages_mirror_the_database_constraint() -> None:
    """Drift here means a valid-looking entry fails at the constraint."""
    assert set(STAGES) == {
        "applied", "recruiter_screen", "phone_screen", "technical", "onsite",
        "offer", "rejected", "accepted", "declined", "withdrawn",
    }
    assert set(KINDS) == {"phone", "video", "in_person", "other"}
