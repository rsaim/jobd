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
    """One interview, five logistics mails -- the live failure shape."""
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


# --- enforce_forward_order ------------------------------------------------
#
# The per-entity timeline call sees the whole chain, so it emits far fewer
# and better events than per-message extraction did -- but it still
# occasionally walks back down the funnel (technical, then recruiter_screen)
# because the subjects genuinely read that way. Ordering is a deterministic
# property of the vocabulary, so it is cheaper and more reliable to enforce
# in code than to keep asking the model for it.
#
# Demoted events are dropped, not reordered: moving an event to where it
# would be monotonic invents a claim the evidence does not support.

from jobd.domain.record import enforce_forward_order  # noqa: E402


def test_forward_sequence_is_untouched() -> None:
    seq = [ev("applied", 0), ev("technical", 10), ev("offer", 20)]
    assert enforce_forward_order(seq) == seq


def test_backwards_step_is_dropped() -> None:
    a, b, c = ev("applied", 0), ev("technical", 10), ev("recruiter_screen", 20)
    assert enforce_forward_order([a, b, c]) == [a, b]


def test_terminal_is_kept_after_any_stage() -> None:
    seq = [ev("technical", 0), ev("rejected", 10)]
    assert enforce_forward_order(seq) == seq


def test_terminal_does_not_block_a_later_terminal() -> None:
    """`accepted` then `declined` is a real sequence -- an offer taken back."""
    seq = [ev("offer", 0), ev("accepted", 10), ev("declined", 20)]
    assert enforce_forward_order(seq) == seq


def test_non_terminal_after_terminal_is_dropped() -> None:
    """The process ended; a later interview claim contradicts it."""
    a, b, c = ev("onsite", 0), ev("rejected", 10), ev("phone_screen", 20)
    assert enforce_forward_order([a, b, c]) == [a, b]


def test_repeated_stage_is_dropped() -> None:
    """Forward means strictly forward: the same stage twice is one milestone."""
    a, b = ev("onsite", 0), ev("onsite", 10)
    assert enforce_forward_order([a, b]) == [a]


def test_ordering_is_by_time_not_input_order() -> None:
    first, second = ev("applied", 0), ev("offer", 30)
    assert enforce_forward_order([second, first]) == [first, second]


def test_empty_is_empty() -> None:
    assert enforce_forward_order([]) == []


# --- onboarding_implies_accepted -----------------------------------------
#
# An offer is routinely made by phone or e-signature and never appears in
# the mailbox. What DOES appear afterwards is employment administration:
# a "Welcome to <company>" thread, payroll and benefits enrolment, I-9/W-4,
# and -- for a visa holder -- months of H-1B/LCA attorney mail. That is
# conclusive evidence the process succeeded, and it is a keyword rule, not a
# judgment call.
#
# It lives in code because the model is not reliable here: on the identical
# 33-message chain, one call answered "accepted" and the next answered
# "technical", at temperature 0. A stage that decides an application's
# outcome cannot be a coin flip, and a deterministic reading of the same
# evidence is both cheaper and reproducible (I3).

from jobd.domain.record import onboarding_accepted_index  # noqa: E402


def test_welcome_thread_is_onboarding() -> None:
    assert onboarding_accepted_index(["Welcome to Acme, Sam!"]) == 0


def test_visa_paperwork_is_onboarding() -> None:
    subjects = ["Re: Next Steps", "LCA Posting (Employee): Action Needed"]
    assert onboarding_accepted_index(subjects) == 1


def test_payroll_and_i9_are_onboarding() -> None:
    assert onboarding_accepted_index(["Your I-9 and W-4 forms"]) == 0
    assert onboarding_accepted_index(["Payroll enrollment"]) == 0


def test_earliest_onboarding_message_wins() -> None:
    """The offer was accepted when onboarding STARTED, not at its last mail."""
    subjects = ["Welcome to Acme!", "Re: Welcome to Acme!", "Your I-9 forms"]
    assert onboarding_accepted_index(subjects) == 0


def test_interview_mail_is_not_onboarding() -> None:
    for subject in [
        "Invitation: Coding Video Interview",
        "You've been invited to join a CoderPad session",
        "Reminder: You have an upcoming interview",
        "Final Interview w/ Acme",
        "Re: Next Steps",
        "Availability Request",
    ]:
        assert onboarding_accepted_index([subject]) is None, subject


def test_rejection_is_not_onboarding() -> None:
    assert onboarding_accepted_index(["Update on your application"]) is None


def test_welcome_to_the_team_newsletter_is_not_onboarding() -> None:
    """'Welcome to our newsletter' must not read as a hire."""
    assert onboarding_accepted_index(["Welcome to the Acme Weekly newsletter"]) is None


def test_empty_chain() -> None:
    assert onboarding_accepted_index([]) is None


# --- ambiguous onboarding keywords need corroboration -----------------------
#
# Immigration/visa/background-check words appear on BOTH sides of the hire
# line: an employer files an H-1B for someone who accepted, and a recruiter
# asks about sponsorship during screening. Matching them alone read a
# pre-screen questionnaire as an acceptance on a real mailbox, for an
# application that was rejected three weeks later.

def test_sponsorship_question_is_not_acceptance():
    assert onboarding_accepted_index(
        ["Immigration Sponsorship Assessment (Voluntary)"]
    ) is None


def test_visa_screening_question_is_not_acceptance():
    assert onboarding_accepted_index(
        ["Quick question: will you require visa sponsorship?"]
    ) is None


def test_background_check_alone_is_not_acceptance():
    assert onboarding_accepted_index(["Background check authorization"]) is None


def test_visa_step_after_welcome_is_acceptance():
    # Visa step corroborated by "Welcome" in the same subject.
    assert onboarding_accepted_index(["Welcome to Acme & Visa Next Steps"]) == 0


def test_immigration_filing_with_petition_is_acceptance():
    # An actual USCIS filing, not a sponsorship question.
    assert onboarding_accepted_index(
        ["Candidate: ACME LLC / US-H1B USCIS COE Petition Filed"]
    ) == 0


def test_unambiguous_onboarding_still_stands_alone():
    for subject in (
        "Welcome to Acme Corp!",
        "Acme Onboarding | Next Steps",
        "Requesting Draft Offer Letter",
    ):
        assert onboarding_accepted_index([subject]) == 0, subject
