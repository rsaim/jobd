"""Stage events are append-only; a timeline shows the newest derivation.

Re-deriving used to destroy the previous timeline, which is backwards for
this system: raw mail is write-once and Postgres is a derived view of it
(I3), so a derivation should be as disposable and as re-runnable as the view
it feeds. Rows now carry the batch that produced them (`derived_at`) and the
reader keeps only the newest batch per application -- appending corrects a
bad pass, and what an earlier pass believed stays on the table to look at.

`latest_batch` is the reader half. It is per *application*, not per company:
one company's applications may have been derived by different runs, and the
one that was skipped must keep showing its older events rather than vanish
because a sibling was re-derived.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from jobd.domain.record import latest_batch

APP_A, APP_B = uuid4(), uuid4()
RUN1 = datetime(2026, 9, 1, tzinfo=UTC)
RUN2 = RUN1 + timedelta(days=1)


class Row:
    """Minimal stand-in: the reader needs an application, a batch, a stage."""

    def __init__(self, app: UUID, batch: datetime | None, stage: str) -> None:
        self.application_id = app
        self.derived_at = batch
        self.stage = stage
        self.occurred_at = RUN1

    def __repr__(self) -> str:  # pragma: no cover - test failure output only
        return f"Row({self.stage}, {self.derived_at})"


def test_newest_batch_wins() -> None:
    old = Row(APP_A, RUN1, "offer")
    new = Row(APP_A, RUN2, "rejected")
    assert latest_batch([old, new]) == [new]


def test_all_rows_of_the_newest_batch_are_kept() -> None:
    keep = [Row(APP_A, RUN2, s) for s in ("applied", "onsite", "offer")]
    assert latest_batch([Row(APP_A, RUN1, "rejected"), *keep]) == keep


def test_applications_are_independent() -> None:
    """A re-derived application must not blank out an untouched sibling."""
    a_new = Row(APP_A, RUN2, "offer")
    b_old = Row(APP_B, RUN1, "applied")
    got = latest_batch([a_new, b_old])
    assert set(got) == {a_new, b_old}


def test_legacy_rows_are_superseded_by_any_derivation() -> None:
    """NULL derived_at is pre-tracking; a real batch always outranks it."""
    legacy = Row(APP_A, None, "offer")
    derived = Row(APP_A, RUN1, "rejected")
    assert latest_batch([legacy, derived]) == [derived]


def test_legacy_rows_survive_when_nothing_derived_them() -> None:
    """An application no pass has reached keeps showing what it showed."""
    legacy = [Row(APP_A, None, "applied"), Row(APP_A, None, "offer")]
    assert latest_batch(legacy) == legacy


def test_empty() -> None:
    assert latest_batch([]) == []
