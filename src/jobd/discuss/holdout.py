"""The experiment the panel voted for, exactly as specified.

All five panelists converged on one test before any model swap: a
stratified 50-message holdout with known labels, GLM-5.2 (the production
extractor, prod-parity settings) against DeepSeek V4 Flash 0731 with
thinking disabled, both in strict-JSON extraction mode, scored on label
accuracy with critical false positives called out separately. Swap rule
from the panel: DS-0731 must be non-inferior (within 2 points) with zero
critical false positives.

Ground truth comes from the record itself — messages whose classification
was settled and survived the audits:
  15 linked positives (recorded to a company by the LLM path)
  10 rulebased positives (ATS-domain mail, deterministic extractor)
  15 human/audit-rejected review-queue items (hard negatives)
  10 prefilter negatives with body text (easy negatives)
Sampling is seeded (setseed) so a re-run scores the same 50 messages.

Run: python -m jobd.discuss.holdout  (env: DATABASE_URL, OPENROUTER_API_KEY)
The verdict is appended to the latest discussion run (round 5, role
'experiment') so the panel page shows what happened next.
"""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import litellm

from jobd.discuss import store
from jobd.domain.extraction import EXTRACTION_SCHEMA, from_payload, render_for_model
from jobd.services.classify import PROMPT

GLM = "openrouter/z-ai/glm-5.2"
DS = "openrouter/deepseek/deepseek-v4-flash-0731"

#: $/Mtok, from openrouter.ai/api/v1/models on 2026-08-17.
PRICE = {GLM: (1.19, 3.74), DS: (0.14, 0.28)}

_SAMPLE = """
WITH pos_llm AS (
    SELECT m.id, 'positive' AS truth, c.canonical_name AS expect_company,
           'llm-positive' AS stratum
    FROM message m JOIN company c ON c.id = m.company_id
    WHERE m.direction = 'inbound' AND length(coalesce(m.body_text,'')) > 200
      AND m.classified_by NOT LIKE 'prefilter%%'
      AND coalesce(m.classified_by,'') <> 'rulebased'
    ORDER BY random() LIMIT 15
), pos_ats AS (
    SELECT m.id, 'positive', c.canonical_name, 'ats-positive'
    FROM message m JOIN company c ON c.id = m.company_id
    WHERE m.classified_by = 'rulebased'
      AND length(coalesce(m.body_text,'')) > 200
    ORDER BY random() LIMIT 10
), neg_rejected AS (
    -- Only rejects corroborated by a learned negative sender rule. The
    -- first run sampled the raw rejected pool and 6 of 15 turned out to be
    -- real job mail rejected in historical bulk passes (a SoFi interview
    -- confirmation among them) — GLM's "critical false positives" were the
    -- ground truth being wrong, not the model. Uncorroborated rejects are
    -- not usable as negative truth.
    SELECT m.id, 'negative', NULL, 'rejected-negative'
    FROM review_queue rq JOIN message m ON m.id = rq.message_id
    WHERE rq.status = 'rejected' AND length(coalesce(m.body_text,'')) > 200
      AND EXISTS (SELECT 1 FROM sender_rule sr
                  WHERE sr.verdict = 'negative'
                    AND ((sr.match_type = 'address'
                          AND lower(m.sender_address) = lower(sr.value))
                     OR (sr.match_type = 'domain'
                         AND lower(coalesce(m.sender_domain,'')) =
                             lower(sr.value))))
    ORDER BY random() LIMIT 15
), neg_prefilter AS (
    SELECT m.id, 'negative', NULL, 'prefilter-negative'
    FROM message m
    WHERE m.classified_by LIKE 'prefilter%%' AND m.company_id IS NULL
      AND length(coalesce(m.body_text,'')) > 200
    ORDER BY random() LIMIT 10
)
SELECT * FROM pos_llm UNION ALL SELECT * FROM pos_ats
UNION ALL SELECT * FROM neg_rejected UNION ALL SELECT * FROM neg_prefilter
"""


def _extract(model: str, rendered: str) -> tuple[dict[str, Any], int, int, float]:
    t0 = time.monotonic()
    kwargs: dict[str, Any] = {}
    if model == DS:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": rendered},
        ],
        max_tokens=1500,
        temperature=0,
        timeout=120,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": EXTRACTION_SCHEMA,
                            "strict": True},
        },
        **kwargs,
    )
    import json

    u = resp.usage
    return (
        json.loads(resp.choices[0].message.content or "{}"),
        int(u.prompt_tokens or 0),
        int(u.completion_tokens or 0),
        time.monotonic() - t0,
    )


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _company_match(expected: str, predicted: str) -> bool:
    e, p = _norm(expected), _norm(predicted)
    return bool(e and p and (e in p or p in e))


def run() -> None:
    conn = store.connect()
    conn.execute("SELECT setseed(0.42)")
    rows = conn.execute(_SAMPLE).fetchall()
    msgs = {
        r[0]: conn.execute(
            "SELECT sender_address, subject, body_text, sent_at"
            " FROM message WHERE id = %s", (r[0],)
        ).fetchone()
        for r in rows
    }
    print(f"sampled {len(rows)} messages", file=sys.stderr)

    results: dict[str, dict[str, Any]] = {}
    for model in (GLM, DS):
        def one(row: Any) -> dict[str, Any]:
            mid, truth, expect_company, stratum = row
            sender, subject, body, sent_at = msgs[mid]
            rendered = render_for_model(
                sender=sender or "", recipient="", subject=subject or "",
                body=(body or "")[:6000],
                date=str(sent_at) if sent_at else None,
            )
            try:
                payload, tin, tout, secs = _extract(model, rendered)
                ex = from_payload(payload)
                pred = ("positive" if ex.is_positive
                        else "negative" if ex.is_negative else "unclassified")
                return {"truth": truth, "pred": pred, "stratum": stratum,
                        "company_ok": _company_match(expect_company or "",
                                                     ex.company_name or ""),
                        "tin": tin, "tout": tout, "secs": secs}
            except Exception as exc:  # noqa: BLE001 — one bad call scores as a punt
                return {"truth": truth, "pred": "error", "stratum": stratum,
                        "company_ok": False, "tin": 0, "tout": 0, "secs": 0.0,
                        "error": str(exc)[:120]}

        with ThreadPoolExecutor(max_workers=8) as pool:
            outs = list(pool.map(one, rows))

        n = len(outs)
        correct = sum(1 for o in outs if o["pred"] == o["truth"])
        crit_fp = sum(1 for o in outs
                      if o["truth"] == "negative" and o["pred"] == "positive")
        fn = sum(1 for o in outs
                 if o["truth"] == "positive" and o["pred"] == "negative")
        punts = sum(1 for o in outs if o["pred"] in ("unclassified", "error"))
        pos = [o for o in outs if o["truth"] == "positive"]
        comp_ok = sum(1 for o in pos if o["company_ok"])
        tin = sum(o["tin"] for o in outs)
        tout = sum(o["tout"] for o in outs)
        pi, po = PRICE[model]
        cost = tin / 1e6 * pi + tout / 1e6 * po
        lat = sorted(o["secs"] for o in outs)[n // 2]
        results[model] = {
            "accuracy": correct / n, "critical_fp": crit_fp, "fn": fn,
            "punts": punts, "company_match": comp_ok / len(pos) if pos else 0,
            "tokens_in": tin, "tokens_out": tout, "cost_usd": round(cost, 5),
            "median_latency_s": round(lat, 1),
            "errors": [o.get("error") for o in outs if o.get("error")][:3],
        }
        print(f"{model}: {results[model]}", file=sys.stderr)

    g, d = results[GLM], results[DS]
    non_inferior = (g["accuracy"] - d["accuracy"] <= 0.02
                    and d["critical_fp"] == 0)
    verdict = (
        "SWAP: DeepSeek V4 Flash 0731 is non-inferior (within 2pp, zero "
        "critical false positives) at "
        f"{PRICE[DS][0]}/{PRICE[DS][1]} vs {PRICE[GLM][0]}/{PRICE[GLM][1]} "
        "$/Mtok — the panel's condition is met."
        if non_inferior else
        "KEEP GLM-5.2: DeepSeek V4 Flash 0731 failed the panel's "
        "non-inferiority bar (>2pp worse, or a critical false positive)."
    )

    def line(name: str, r: dict[str, Any]) -> str:
        return (f"{name}: accuracy {r['accuracy']:.0%}, "
                f"critical FP {r['critical_fp']}, FN {r['fn']}, "
                f"punts {r['punts']}, company match {r['company_match']:.0%}, "
                f"{r['tokens_in']}+{r['tokens_out']} tok, ${r['cost_usd']}, "
                f"median {r['median_latency_s']}s")

    report = "\n".join([
        "HOLDOUT RESULT (50 messages, seeded stratified sample):",
        line("GLM 5.2 (prod)", g),
        line("DS V4 Flash 0731 (thinking off)", d),
        "",
        verdict,
    ])
    print(report)

    t = store.transcript(conn)
    if t:
        run_id = t[0]["run_id"]
        store.add(conn, run_id=run_id, seq=t[-1]["seq"] + 1, round_no=5,
                  model="holdout", display="The Experiment", role="experiment",
                  content=report, meta={"glm": g, "ds": d,
                                        "non_inferior": non_inferior})
    conn.close()


if __name__ == "__main__":
    run()
