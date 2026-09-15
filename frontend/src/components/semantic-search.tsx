/**
 * Search the record by meaning.
 *
 * The keyword tab next door answers "which messages contain these words".
 * This one answers "which messages are about this", which is a different
 * question and, for a mailbox nobody wrote with search in mind, usually the
 * one you actually have. Nobody writes "compensation" in the subject line;
 * they write "$150K–$300K + Equity".
 *
 * Three things are on screen that a plain result list would leave out, and
 * each is here because without it the reader would draw a wrong conclusion:
 *
 * 1. COVERAGE. Semantic search can only see embedded messages — today the
 *    recorded ones, a fraction of the mailbox. An empty result therefore
 *    means "nothing embedded matches", never "the record contains nothing
 *    about this", and the difference is a number.
 *
 * 2. SHARED WORDS. Per hit, which of the words you typed actually occur in
 *    it — marked in the preview text itself, so the claim is visible rather
 *    than asserted. A hit with none is one full-text search could not have
 *    returned at all.
 *
 * 3. WHO. The companies the hits belong to, counted. "Which of them keeps
 *    talking about relocation" is a question the ranked list buries and a
 *    tally answers instantly; clicking one re-runs the query scoped to it.
 *
 * LIVE, BUT PACED. Results follow the box as you type. Every typed query is
 * one embedding call against a paid API, so the box is debounced (it waits
 * for you to stop typing) *and* throttled (never more than one call per
 * window, however fast you type), with a floor on query length. Enter skips
 * the wait. The state of that pacing is on screen — "waiting", "embedding" —
 * because a box that silently decides when to spend money should at least
 * say what it is doing. "More like this" costs nothing at all: it probes
 * with a message's own stored vector.
 *
 * The results are a fluid grid, not a column: card width is fixed and the
 * column count is whatever the viewport affords, so a wide screen shows more
 * results rather than more whitespace. Opening a card spans it across the
 * full grid, because reading a message wants the width more than the list
 * does.
 */

import { useEffect, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  ArrowDownLeft,
  ArrowUpRight,
  Building2,
  ChevronDown,
  Compass,
  Radar,
  Sparkles,
  X,
} from "lucide-react"

import { useSemanticSearch } from "@/lib/api"
import type { SearchHit } from "@/lib/api"
import { fmtIso } from "@/lib/record"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Spinner } from "@/components/ui/spinner"
import { Marked } from "@/components/highlight"
import { MessageBody } from "@/components/message-row"
import { EmptyState, ErrorState, SectionHead } from "@/components/page"

/* ------------------------------------------------------------------ *
 * The probes.
 *
 * Not example syntax — actual questions a job search has, written as
 * sentences, chosen because each one is a concept the mailbox expresses in
 * words the reader would never think to type. "Turned down" appears as
 * "we've decided to move forward with other candidates"; pay appears as a
 * number with a dollar sign in front of it. Every one of these returns hits
 * that share no words with the query, which is the point being made.
 * ------------------------------------------------------------------ */
const PROBES: { label: string; query: string }[] = [
  { label: "Being turned down", query: "a polite way of turning me down" },
  { label: "What it pays", query: "how much does this role pay" },
  { label: "Take-home", query: "they want me to do a take-home coding exercise" },
  { label: "Scheduling", query: "finding a time to talk, proposing slots or a calendar link" },
  { label: "An offer", query: "an offer is being extended to me" },
  { label: "Visa", query: "visa status, sponsorship, or right to work" },
  { label: "Relocation", query: "moving cities, relocation, or being expected on site" },
  { label: "Role pulled", query: "the role was cancelled, frozen, or filled internally" },
  { label: "Chasing me", query: "following up because I never replied" },
  { label: "A referral", query: "someone asking me for an introduction or a referral" },
]

/** How the box spends money.
 *
 *  `SETTLE` is the debounce: the pause after the last keystroke that means
 *  "they stopped typing". `GAP` is the throttle: the minimum spacing between
 *  two calls no matter how the typing is shaped — hold backspace on a long
 *  query and the debounce alone would fire a call per pause, so the two are
 *  both needed rather than being two names for one timer. `FLOOR` keeps
 *  fragments out; two letters embed to noise and still cost a call. */
const SETTLE = 500
const GAP = 1200
const FLOOR = 3
/** A grid of cards holds more than a list of rows does, and the extra rows
 *  are free: the embedding call is per query, not per hit. */
const LIMIT = 24

/** Cosine similarity, drawn on a fixed band.
 *
 *  The band is 0.20–0.65, not 0–1, and it is fixed rather than normalised
 *  against the best hit in the set: real text embeddings almost never score
 *  outside it, so a 0–1 bar would render every result as a barely-filled
 *  stub, and a bar normalised per-search would make rank 1 look identical
 *  whether it was an excellent match or the best of a bad lot. A fixed band
 *  means the same bar length means the same thing across two searches. */
const BAND_LOW = 0.2
const BAND_HIGH = 0.65

function Closeness({ similarity }: { similarity: number }) {
  const fill = Math.max(0, Math.min(1, (similarity - BAND_LOW) / (BAND_HIGH - BAND_LOW)))
  return (
    <div
      className="flex shrink-0 items-center gap-1.5"
      title={`Cosine similarity ${similarity.toFixed(3)}. The bar spans ${BAND_LOW}–${BAND_HIGH}, where real embeddings land.`}
    >
      <div className="bg-muted h-1.5 w-12 overflow-hidden rounded-full">
        <div className="bg-primary h-full rounded-full" style={{ width: `${fill * 100}%` }} />
      </div>
      <span className="tabular text-muted-foreground text-[11px]">
        {similarity.toFixed(2)}
      </span>
    </div>
  )
}

/** One preview line's worth of body, with the newlines and the NUL bytes a
 *  scraped mailbox carries flattened out of it. */
function preview(body: string | null | undefined): string {
  if (!body) return ""
  return body.replace(/\u0000/g, " ").replace(/\s+/g, " ").trim().slice(0, 400)
}

function HitCard({
  hit,
  rank,
  typed,
  onMoreLikeThis,
}: {
  hit: SearchHit
  rank: number
  /** Whether this result set came from typed words. "Shares no words with
   *  your query" is vacuously true of every neighbour result — there was no
   *  query — and printing it there would be a claim about nothing. */
  typed: boolean
  onMoreLikeThis: () => void
}) {
  const [open, setOpen] = useState(false)
  const blind = hit.shared_words.length === 0
  const snippet = preview(hit.body_text)

  return (
    <Card
      className={cn(
        "panel panel-lift group gap-0 overflow-hidden rounded-xl py-0",
        // Reading wants the width; the list does not. An opened card takes
        // the whole row rather than stretching every card beside it.
        open && "col-span-full",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="w-full px-3.5 pt-3 pb-2 text-left"
      >
        <span className="flex items-start gap-2">
          {/* The rank is real information here — the list is ordered by
              distance and nothing else — so it gets a number, not a bullet. */}
          <span className="tabular text-muted-foreground/70 mt-px w-5 shrink-0 text-right text-[11px]">
            {rank}
          </span>
          {hit.direction === "inbound" ? (
            <ArrowDownLeft
              className="mt-0.5 size-3 shrink-0"
              style={{ color: "var(--status-good)" }}
            />
          ) : (
            <ArrowUpRight
              className="mt-0.5 size-3 shrink-0"
              style={{ color: "var(--status-warning)" }}
            />
          )}
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
            {hit.subject || "(no subject)"}
          </span>
          <Closeness similarity={hit.similarity} />
          <ChevronDown
            className={cn(
              "text-muted-foreground mt-0.5 size-3.5 shrink-0 transition-transform",
              open && "rotate-180",
            )}
          />
        </span>

        {/* `line-clamp-3` is the whole reason a card can hold a preview at
            all: it sets `display: -webkit-box`, so no `block` beside it. */}
        {snippet && !open && (
          <span className="text-muted-foreground mt-1.5 line-clamp-3 pl-7 text-[12px] leading-relaxed">
            {/* The shared-words count made visible: a card with nothing
                marked is one the embedding found and full-text could not. */}
            <Marked text={snippet} words={hit.shared_words} />
          </span>
        )}
      </button>

      {/* `mt-auto`: cards in a grid row stretch to the tallest one, so the
          meta line rides the card's floor instead of leaving a hole under a
          short preview. */}
      <div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 pb-2.5 pl-[38px] text-[11.5px]">
        <span className="tabular">{fmtIso(hit.sent_at)}</span>
        {hit.company_id && (
          <Link
            to={`/company/${hit.company_id}`}
            className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
          >
            <Building2 className="size-3" />
            {hit.company_name}
          </Link>
        )}
        {typed && blind && (
          <span
            className="border-primary/30 bg-primary/[0.07] text-primary rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium"
            title="None of the words you typed appear in this message. Full-text search could not have returned it."
          >
            no shared words
          </span>
        )}
        {/* One per card, two dozen cards: always-on it is a grid of identical
            buttons competing with the results. Revealed on hover or keyboard
            focus, and kept visible while the card is open. */}
        <Button
          size="xs"
          variant="ghost"
          className={cn(
            "ml-auto h-6 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
            open && "opacity-100",
          )}
          onClick={onMoreLikeThis}
          title="Search using this message's own vector — no model call"
        >
          <Radar /> More like this
        </Button>
      </div>

      {open && <MessageBody id={hit.id} />}
    </Card>
  )
}

export function SemanticSearch() {
  const [params, setParams] = useSearchParams()
  const like = params.get("like") ?? ""
  const company = params.get("company") ?? ""

  const [draft, setDraft] = useState(() => params.get("q") ?? "")
  const [settled, setSettled] = useState(() => params.get("q") ?? "")
  const lastCall = useRef(0)

  /** One writer for the address bar, as everywhere else — a search is a URL,
   *  so a result set can be pasted to someone (M7 gate 1). Written from the
   *  *settled* query, never from the keystroke: the address bar should say
   *  what was actually searched. */
  const set = (entries: Record<string, string>) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(entries)) {
          if (value) next.set(key, value)
          else next.delete(key)
        }
        return next
      },
      { replace: true },
    )
  }

  /** Debounce and throttle in one timer: wait `SETTLE` after the last
   *  keystroke, but never land inside `GAP` of the previous call. */
  useEffect(() => {
    if (draft === settled) return
    const since = Date.now() - lastCall.current
    const timer = window.setTimeout(
      () => {
        lastCall.current = Date.now()
        setSettled(draft)
        set({ q: draft, like: "" })
      },
      Math.max(SETTLE, GAP - since),
    )
    return () => window.clearTimeout(timer)
    // `set` closes over setParams, which react-router keeps stable, and the
    // functional update means a stale `params` snapshot cannot clobber a
    // company filter changed in the meantime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, settled])

  /** Enter, or a probe chip: skip the wait entirely. The pacing exists to
   *  keep half-typed words from costing a call, and neither of these is a
   *  half-typed word. */
  const runNow = (text: string) => {
    lastCall.current = Date.now()
    setDraft(text)
    setSettled(text)
    set({ q: text, like: "" })
  }

  const active = settled.trim().length >= FLOOR
  const query = new URLSearchParams()
  if (like) query.set("like", like)
  else if (active) query.set("q", settled)
  if (company && !like) query.set("company", company)
  query.set("limit", String(LIMIT))

  const { data, isFetching, error } = useSemanticSearch(
    `?${query}`,
    Boolean(like) || active,
  )

  const waiting = draft !== settled
  const hits = data?.hits ?? []
  const blind = hits.filter((hit) => hit.shared_words.length === 0).length
  const coverage = data?.coverage
  const short = draft.trim().length > 0 && draft.trim().length < FLOOR

  return (
    <div className="mt-4 space-y-4">
      <Card className="panel bg-card/85 z-[5] rounded-xl py-3.5 backdrop-blur-md sm:sticky sm:top-14">
        <CardContent className="space-y-3 px-4">
          <div className="flex flex-wrap items-center gap-2">
            {/* min-w-72 is 288px, which with the border and the inline-end
                addon overruns a 320px phone. The floor only matters once
                there is room for it. */}
            <InputGroup className="h-9 w-full flex-1 sm:min-w-72">
              <InputGroupAddon>
                <Sparkles className="size-3.5" />
              </InputGroupAddon>
              <InputGroupInput
                value={draft}
                placeholder="Describe what you're looking for, in your own words"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") runNow(event.currentTarget.value)
                }}
              />
              <InputGroupAddon align="inline-end">
                {/* The pacing, said out loud. A live box that decides on its
                    own when to spend a call should show which of the two
                    waits you are in. */}
                {isFetching ? (
                  <span className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
                    <Spinner className="size-3" /> embedding
                  </span>
                ) : waiting ? (
                  <span className="text-muted-foreground flex items-center gap-1.5 text-[11px]">
                    <span className="bg-primary/60 size-1.5 animate-pulse rounded-full" />
                    {short ? `${FLOOR} letters minimum` : "waiting for a pause"}
                  </span>
                ) : (
                  <InputGroupButton
                    size="sm"
                    variant="ghost"
                    className="text-muted-foreground"
                    disabled={!draft.trim()}
                    onClick={() => runNow(draft)}
                  >
                    Search now
                  </InputGroupButton>
                )}
              </InputGroupAddon>
            </InputGroup>
            {(draft || like) && (
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={() => {
                  lastCall.current = Date.now()
                  setDraft("")
                  setSettled("")
                  set({ q: "", like: "", company: "" })
                }}
              >
                <X /> Clear
              </Button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground mr-1 font-mono text-[10px] tracking-[0.14em] uppercase">
              Try
            </span>
            {PROBES.map((probe) => (
              <button
                key={probe.label}
                type="button"
                onClick={() => runNow(probe.query)}
                title={probe.query}
                className={cn(
                  // py-0.5 makes these 23px tall — under a thumb. They get a
                  // real target on touch and stay compact on a pointer.
                  "hover:border-primary/40 hover:text-foreground text-muted-foreground min-h-8 rounded-md border px-2.5 py-1.5 text-[11.5px] transition-colors sm:min-h-0 sm:px-2 sm:py-0.5",
                  settled === probe.query &&
                    "border-primary/50 bg-primary/[0.07] text-foreground",
                )}
              >
                {probe.label}
              </button>
            ))}
          </div>

          {coverage && (
            <p className="text-muted-foreground text-[11.5px]">
              Searching{" "}
              <b className="tabular text-foreground font-semibold">
                {coverage.embedded.toLocaleString()}
              </b>{" "}
              embedded messages of {coverage.total.toLocaleString()} — the recorded
              ones. Anything the classifier never resolved to a company has no vector
              and cannot appear here;{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono text-[10.5px]">
                jobd embed-backfill
              </code>{" "}
              widens it.
            </p>
          )}
        </CardContent>
      </Card>

      {error && <ErrorState error={error} />}

      {data?.like && (
        <div className="border-primary/30 bg-primary/[0.06] flex flex-wrap items-center gap-2 rounded-xl border px-3.5 py-2.5 text-[12.5px]">
          <Radar className="text-primary size-4 shrink-0" />
          <span className="min-w-0">
            Messages like{" "}
            <b className="font-medium">{data.like.subject || "(no subject)"}</b> — probed
            with its own stored vector, no model call.
          </span>
          <Button
            size="xs"
            variant="ghost"
            className="ml-auto"
            onClick={() => set({ like: "" })}
          >
            <X /> Back to the query
          </Button>
        </div>
      )}

      {hits.length > 0 && (
        <>
          <SectionHead icon={Compass} count={hits.length}>
            Closest in meaning
          </SectionHead>

          <div className="-mt-1 flex flex-wrap items-center gap-x-4 gap-y-2">
            <p className="text-muted-foreground text-[12.5px]">
              {data?.like ? (
                <>
                  Ranked by distance from that message's own vector. A tight cluster
                  here means the record really does say the same thing repeatedly.
                </>
              ) : (
                <>
                  <b className="text-foreground font-semibold">{blind}</b> of{" "}
                  {hits.length} share no words with your query — full-text search could
                  not have found {blind === 1 ? "it" : "them"}.
                </>
              )}
            </p>

            {data && data.companies.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {data.companies.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => set({ company: company === entry.id ? "" : entry.id })}
                    className={cn(
                      "hover:border-primary/40 flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11.5px] transition-colors",
                      company === entry.id && "border-primary/50 bg-primary/[0.07]",
                    )}
                    title={
                      company === entry.id
                        ? "Search the whole record again"
                        : `Re-run this query inside ${entry.name}`
                    }
                  >
                    {entry.name}
                    <span className="tabular text-muted-foreground">{entry.count}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Fixed card width, fluid column count: the same result set is two
              columns on a laptop and five on a wide monitor, rather than one
              column and a field of empty space. */}
          <div
            className={cn(
              "grid grid-cols-[repeat(auto-fill,minmax(20rem,1fr))] gap-3",
              isFetching && "opacity-60 transition-opacity",
            )}
          >
            {hits.map((hit, index) => (
              <HitCard
                key={hit.id}
                hit={hit}
                rank={index + 1}
                typed={!data?.like}
                onMoreLikeThis={() => set({ like: hit.id })}
              />
            ))}
          </div>
        </>
      )}

      {data && hits.length === 0 && !isFetching && (
        <EmptyState title="Nothing embedded is close to that" icon={Compass}>
          {data.like
            ? "That message has no embedding, so it has no neighbours to offer."
            : "Every embedded message is far from this query. Widen the wording, or widen coverage with jobd embed-backfill."}
        </EmptyState>
      )}

      {!data && !isFetching && (
        <EmptyState title="Ask it the way you'd say it" icon={Sparkles}>
          Results follow the box as you type. The keyword tab finds the words you
          type; this one finds what you mean — "how much does this role pay" reaches a
          subject line reading "$150K–$300K + Equity", which shares none of those
          words.
        </EmptyState>
      )}
    </div>
  )
}
