"""The stage timeline is derived per application, not asserted per message.

Per-message stage extraction is structurally unable to get this right: a
single "Confirming Zoom Conversation" is genuinely ambiguous in isolation --
recruiter screen, technical round or onsite all fit -- and only the sequence
disambiguates it. Worse, every `Re:` reply in a thread is asked the same
question independently, so one real event becomes eight assertions. Against
the live corpus that was 280 of 833 stage events (33.6%) redundant, and
`accepted` -- an outcome that can happen at most once -- appeared 67 times
across 13 applications.

`collapse_stage_runs` is the deduplicating half of the answer: consecutive
observations of the *same* stage describe one event, so they fold to the
earliest of the run (the moment it is first evidenced). It is deliberately
NOT monotonicity enforcement -- a genuine second recruiter screen with a
different team must survive, so a later run of an *earlier* stage is kept.
That trade is why this is a separate, unambiguous step.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from jobd.domain.record import StageEvent, collapse_stage_runs

APP = uuid4()
T0 = datetime(2025, 11, 25, 9, 0, tzinfo=UTC)


def ev(stage: str, minutes: int, msg: UUID | None = None) -> StageEvent:
    return StageEvent(
        application_id=APP,
        stage=stage,  # type: ignore[arg-type]
        occurred_at=T0 + timedelta(minutes=minutes),
        evidence_message_id=msg or uuid4(),
        extracted_by="llm",
    )


def test_empty_is_empty() -> None:
    assert collapse_stage_runs([]) == []


def test_single_event_survives() -> None:
    one = ev("applied", 0)
    assert collapse_stage_runs([one]) == [one]


def test_thread_replies_collapse_to_one_event() -> None:
    """The live failure: eight `Re:` siblings, one real onsite."""
    run = [ev("onsite", i) for i in range(8)]
    got = collapse_stage_runs(run)
    assert len(got) == 1
    assert got[0] is run[0], "the earliest evidence of the run is kept"


def test_distinct_stages_all_survive() -> None:
    seq = [ev("applied", 0), ev("recruiter_screen", 10), ev("onsite", 20)]
    assert collapse_stage_runs(seq) == seq


def test_only_consecutive_runs_collapse() -> None:
    """A real process can revisit a stage; only adjacent repeats are one event."""
    a, b, c = ev("onsite", 0), ev("technical", 10), ev("onsite", 20)
    got = collapse_stage_runs([a, b, c])
    assert [g.stage for g in got] == ["onsite", "technical", "onsite"]
    assert got == [a, b, c]


def test_input_order_does_not_matter() -> None:
    """Callers pass whatever the DB handed them; the reducer sorts by time."""
    first, second = ev("applied", 0), ev("onsite", 30)
    assert collapse_stage_runs([second, first]) == [first, second]


def test_accepted_asserted_repeatedly_folds_to_one() -> None:
    """`accepted` can happen at most once; 67-across-13-apps was the symptom."""
    got = collapse_stage_runs([ev("accepted", i) for i in range(5)])
    assert len(got) == 1


def test_original_events_are_not_mutated() -> None:
    seq = [ev("onsite", 0), ev("onsite", 5)]
    before = list(seq)
    collapse_stage_runs(seq)
    assert seq == before


# --- resolve_stage_window -------------------------------------------------
#
# Collapse fixes repeats of the *same* stage. It cannot fix the other half of
# the per-message failure: one interview generates an invite, an update, a
# reminder and a confirmation, and each is independently guessed at, so the
# timeline oscillates between adjacent stages within hours. On the live
# corpus 29% of stage evidence is scheduling logistics, and 35% of
# transitions land inside one day (p10 = 15 minutes) -- a real process does
# not advance a stage every fifteen minutes.
#
# So within a short window, the furthest-along claim wins: the recruiter who
# says "technical interview" outranks the calendar bot that says "quick
# sync", because the specific claim carries information the generic one does
# not. Terminal stages are never dropped -- an ending is not chatter.

from jobd.domain.record import resolve_stage_window  # noqa: E402


def test_window_keeps_furthest_stage_among_same_day_chatter() -> None:
    """The live Datadog case: one interview, five logistics mails."""
    seq = [
        ev("phone_screen", 0),
        ev("onsite", 60),
        ev("recruiter_screen", 120),
        ev("phone_screen", 180),
    ]
    got = resolve_stage_window(seq, within_hours=24)
    assert [g.stage for g in got] == ["onsite"]


def test_window_does_not_merge_across_a_real_gap() -> None:
    seq = [ev("recruiter_screen", 0), ev("onsite", 60 * 24 * 9)]
    got = resolve_stage_window(seq, within_hours=24)
    assert [g.stage for g in got] == ["recruiter_screen", "onsite"]


def test_terminal_stage_is_never_swallowed_by_chatter() -> None:
    """A rejection landing beside interview logistics is still the outcome."""
    seq = [ev("onsite", 0), ev("rejected", 30)]
    got = resolve_stage_window(seq, within_hours=24)
    assert "rejected" in [g.stage for g in got]


def test_terminal_does_not_absorb_a_later_distinct_stage() -> None:
    seq = [ev("rejected", 0), ev("applied", 60 * 24 * 30)]
    got = resolve_stage_window(seq, within_hours=24)
    assert [g.stage for g in got] == ["rejected", "applied"]


def test_window_keeps_earliest_time_of_the_merged_run() -> None:
    seq = [ev("phone_screen", 0), ev("onsite", 120)]
    got = resolve_stage_window(seq, within_hours=24)
    assert got[0].occurred_at == seq[0].occurred_at
    assert got[0].stage == "onsite"


def test_window_is_idempotent() -> None:
    seq = [ev("phone_screen", 0), ev("onsite", 60), ev("technical", 90)]
    once = resolve_stage_window(seq, within_hours=24)
    assert resolve_stage_window(once, within_hours=24) == once


def test_empty_and_single() -> None:
    assert resolve_stage_window([], within_hours=24) == []
    one = [ev("applied", 0)]
    assert resolve_stage_window(one, within_hours=24) == one
