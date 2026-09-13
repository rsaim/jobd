"""The demo classifies itself: gold replay through the real extract port.

`jobd demo` needs no model because the synthetic corpus's truth is known by
construction — `GoldReplayExtractor` answers extraction calls from
`demo_gold.GOLD`. These tests pin the lookup: every one of the 109 demo
messages must resolve (single-message and thread renders), the payloads
must match gold, and the agency threads must split agency from client the
way the thread schema expects.
"""

from jobd.domain.extraction import (
    EXTRACTION_SCHEMA,
    THREAD_EXTRACTION_SCHEMA,
    render_for_model,
)
from jobd.services.demo import DEMO_ACCOUNT, GoldReplayExtractor, _MAILS
from jobd.services.demo_gold import GOLD


def _render(mail) -> str:
    return render_for_model(
        sender=mail.sender, recipient=mail.to, subject=mail.subject, body=mail.body
    )


def test_every_demo_message_resolves() -> None:
    x = GoldReplayExtractor()
    for mail, gold in zip(_MAILS, GOLD, strict=True):
        got = x.extract("p", EXTRACTION_SCHEMA, text=_render(mail))
        assert got["label"] == ("positive" if gold.job_related else "negative")
        assert got["stage"] == gold.stage
        assert got["company_name"] == gold.company


def test_thread_render_reads_the_latest_sibling() -> None:
    x = GoldReplayExtractor()
    # Two Anthropic loop siblings concatenated, oldest first — the reading
    # must be the later one's (the offer), as a live model reading a quoted
    # chain would report the thread's furthest state.
    idx = [i for i, g in enumerate(GOLD) if g.company == "Anthropic" and g.stage]
    early, late = idx[0], idx[-1]
    text = _render(_MAILS[early]) + "\n\n" + _render(_MAILS[late])
    got = x.extract("p", THREAD_EXTRACTION_SCHEMA, text=text)
    assert got["stage"] == GOLD[late].stage


def test_agency_thread_splits_agency_from_client() -> None:
    x = GoldReplayExtractor()
    agency_mails = [
        (m, g)
        for m, g in zip(_MAILS, GOLD, strict=True)
        if g.job_related
        and g.company
        and m.sender != DEMO_ACCOUNT
        and ".example" in m.sender
    ]
    assert agency_mails, "demo lost its agency arc"
    mail, gold = agency_mails[0]
    got = x.extract("p", THREAD_EXTRACTION_SCHEMA, text=_render(mail))
    assert got["company_name"] == gold.company          # the client employer
    assert got["company_domain"] is None                 # never the agency's
    assert got["agency_domain"] == mail.sender.rsplit("@", 1)[-1]
    assert got["client_companies"] == [
        {"name": gold.company, "domain": None, "role_title": None}
    ]


def test_unmatched_render_fails_loudly() -> None:
    x = GoldReplayExtractor()
    import pytest

    with pytest.raises(ValueError, match="no demo message matches"):
        x.extract("p", EXTRACTION_SCHEMA, text="Subject: not a demo mail")
