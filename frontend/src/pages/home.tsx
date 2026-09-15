/**
 * What needs you, and nothing else.
 *
 * One list — Review — and it comes FIRST. It used to be three separately
 * named, separately iconned sections ("Waiting on you", "Going cold", "In
 * flight"), which asked a reader to learn three category boundaries before
 * they could answer "what do I actually need to do right now" — the three
 * were a taxonomy of *why* a row needs you, not a distinction that changes
 * what you do about it. They're merged: anything that needs a reply from
 * you, or is about to go silent long enough to read as ghosted, lands in
 * Review, last touch first (re-sortable by any measured column — the
 * ordering lives in BriefingTable). A process that is simply moving — no action
 * due — needs no row here at all; it stays visible on Companies, and only
 * its count survives, in the stats band below. The counts, the graph and
 * the queue depth are context and sit underneath: a page opened twenty
 * times a day should open on the work, not on a scoreboard.
 *
 * The list is a table. Fifty cards is a page of boxes read one at a time,
 * and the questions this page gets are comparisons — who has been quiet
 * longest, which of these is at technical, what moved most recently — which
 * is to say they are columns.
 *
 * Every row carries the silence rail, which is the one measurement this
 * record has that a CRM does not: not "12 days" as a number to interpret, but
 * 12 days drawn against the thresholds the app itself acts on — and down a
 * column, one rail against the next, it reads as a comparison at a glance.
 */

import { useEffect, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import {
  ArrowUpRight,
  CalendarRange,
  Eye,
  ListFilter,
  ShieldCheck,
  Share2,
  Sparkles,
  Sunrise,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react"

import { useActivity, useHome } from "@/lib/api"
import { useRange } from "@/lib/range-context"
import { rangeParams } from "@/lib/range"
import type { ActivityCalendar, ActivityMetric, BriefingRow, Stats } from "@/lib/api"
import { pct } from "@/lib/record"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Kbd } from "@/components/ui/kbd"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ActivityGraph } from "@/components/activity-graph"
import { BriefingTable } from "@/components/briefing-table"
import { matches, terms } from "@/components/highlight"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
  Stat,
} from "@/components/page"

/** The activity graph plus its metric picker — one panel of the top row.
 *  "Interview days" (confirmed rounds) is the default and arrives with the
 *  page payload; switching to "Email traffic" fetches the other series from
 *  /api/activity without reloading anything else. */
function SearchPulse({ initial }: { initial: ActivityCalendar }) {
  const [metric, setMetric] = useState<ActivityMetric>("interviews")
  const { range } = useRange()
  const fetched = useActivity(metric, undefined, Object.fromEntries(rangeParams(range)))
  const calendar = metric === "interviews" ? initial : fetched.data
  return (
    <div className="flex h-full flex-col justify-center gap-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground flex items-center gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase">
          <CalendarRange className="size-3" /> Activity
        </span>
        <Button
          asChild
          variant="ghost"
          size="sm"
          className="text-muted-foreground ml-auto h-7 px-2 text-[11px]"
        >
          <Link to="/share" title="A chrome-free card of this graph and the headline numbers, sized for a feed screenshot.">
            <Share2 className="size-3.5" /> Share card
          </Link>
        </Button>
        <Select
          value={metric}
          onValueChange={(value) => setMetric(value as ActivityMetric)}
        >
          <SelectTrigger size="sm" className="h-7 w-auto text-[11.5px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="interviews">Interview days</SelectItem>
            <SelectItem value="messages">Email traffic</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {calendar ? (
        <ActivityGraph calendar={calendar} />
      ) : (
        <p className="text-muted-foreground text-[12px]">Loading the series…</p>
      )}
    </div>
  )
}

/** The search as a conversion machine, one strip: each stage's count with a
 *  gauge showing what fraction of the PREVIOUS stage made it here. Widths are
 *  per-step conversions, not shares of the total — 364 companies against
 *  7 offers on one linear scale would render the right half of the strip
 *  invisible, and the question each gauge answers ("of those, how many?") is
 *  the per-step one anyway.
 *
 *  Every stage counts COMPANIES. The search is a search for an employer, so
 *  one company applied to four times is one company that either interviewed
 *  you or did not; counting applications let a single employer contribute
 *  four times to every gauge and put 13 in the offers slot where seven
 *  companies had made an offer. Application volume rides along in the first
 *  stage's hint rather than as a step of its own. */
function FunnelStrip({ stats }: { stats: Stats }) {
  const stages = [
    {
      label: "Companies",
      n: stats.total_companies,
      hint: `Every company on record, including agencies and unanswered recruiter pitches — across ${stats.total_applications} applications.`,
    },
    {
      label: "Engaged",
      n: stats.engaged,
      hint: "Companies where a real conversation existed: you wrote to them, or the process reached a stage no single email can mint.",
    },
    {
      label: "Replied",
      n: stats.replied,
      hint: "Companies where an inbound message arrived after your first outbound one — they actually answered you.",
    },
    {
      label: "Interviewed",
      n: stats.interviewed,
      hint: "Companies that ran at least one confirmed interview round.",
    },
    {
      label: "Offers",
      n: stats.offers,
      hint: "Companies that ever extended an offer, however it ended.",
    },
  ]
  return (
    <Card className="panel grid grid-cols-5 gap-0 rounded-xl px-1 py-3">
      {stages.map((stage, i) => {
        const prev = i > 0 ? stages[i - 1].n : 0
        const conv = prev > 0 ? stage.n / prev : null
        const last = i === stages.length - 1
        return (
          <div
            key={stage.label}
            title={stage.hint}
            className={`px-3 ${i > 0 ? "border-border/60 border-l" : ""}`}
          >
            <p className="text-muted-foreground truncate font-mono text-[9.5px] tracking-[0.12em] uppercase">
              {stage.label}
            </p>
            <div
              className="tabular mt-1.5 text-[20px] leading-none font-semibold tracking-tight"
              style={last && stage.n > 0 ? { color: "var(--status-good)" } : undefined}
            >
              {stage.n}
            </div>
            {conv !== null ? (
              <div className="mt-2.5 flex items-center gap-1.5">
                <div className="bg-muted h-1 min-w-0 flex-1 overflow-hidden rounded-full">
                  <div
                    className="bg-primary/70 h-full rounded-full"
                    style={{ width: `${Math.max(2, Math.round(conv * 100))}%` }}
                  />
                </div>
                <span className="text-muted-foreground shrink-0 font-mono text-[9.5px]">
                  {Math.round(conv * 100)}%
                </span>
              </div>
            ) : (
              <p className="text-muted-foreground/70 mt-2.5 font-mono text-[9.5px]">
                everything on record
              </p>
            )}
          </div>
        )
      })}
    </Card>
  )
}

/** Everything in a row that a reader might type at it. Stage and outcome
 *  are in here on purpose: "onsite" and "ghosted" are words people filter by,
 *  and they are printed on the row even though they are not prose. */
const haystack = (row: BriefingRow) =>
  [
    row.canonical_name,
    row.role_title ?? "",
    row.last_subject ?? "",
    row.status,
    row.latest_stage ?? "",
    row.kind,
  ].join(" ")

export function HomePage() {
  const { query } = useRange()
  const { data, isPending, error } = useHome(query)
  const [params, setParams] = useSearchParams()
  const [find, setFind] = useState(() => params.get("find") ?? "")
  const box = useRef<HTMLInputElement>(null)

  /** The filter runs on data already in memory, so it is applied on the
   *  keystroke — no request, nothing to debounce. The address bar is the
   *  slow half: writing it per keystroke buys nothing, so it lags behind by
   *  a beat and lands where the typing stopped. */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (find) next.set("find", find)
          else next.delete("find")
          return next
        },
        { replace: true },
      )
    }, 250)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [find])

  /** `/` is the filter, as it is in every list-shaped tool. Escape leaves. */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable
      if (event.key === "/" && !typing) {
        event.preventDefault()
        box.current?.focus()
      }
      if (event.key === "Escape" && target === box.current) {
        setFind("")
        box.current?.blur()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const words = terms(find)
  const filtering = words.length > 0
  const keep = (row: BriefingRow) => matches(haystack(row), words)

  // One list, not three: a reply overdue and a thread about to go quiet are
  // both "needs you". Ordering belongs to the table now (BriefingTable
  // defaults to last touch, newest first, with every measured column a
  // click away) — sorting here would be overridden on render. `in_flight`
  // still gets fetched — its count feeds the "Moving" stat below — but a
  // process with no action due gets no row here.
  const review = [...data.waiting, ...data.cold].filter(keep)
  const shown = review.length
  const held = data.waiting.length + data.cold.length

  // The lede is the page's one sentence, and it is assembled from the record
  // rather than written: a fixed strapline would keep claiming there was work
  // to do on a morning when there wasn't.
  const demand = review.length
    ? `${review.length} ${review.length === 1 ? "process needs" : "processes need"} your review`
    : "Nothing needs your review right now"
  const moving = data.moving_count

  return (
    <div className="space-y-2">
      {/* "Today", never "Home" — the nav already places you; the title's job
          is to say what this page is: the day's briefing on the search. */}
      <PageHead
        icon={Sunrise}
        eyebrow="Your search, briefed"
        title="Today"
        lede={
          <>
            {demand}. {moving} {moving === 1 ? "process is" : "processes are"} still
            moving, no action due.
          </>
        }
        actions={
          data.counts.pending ? (
            <Button asChild size="sm">
              <Link to="/triage">
                Review {data.counts.pending} extraction
                {data.counts.pending === 1 ? "" : "s"}
                <ArrowUpRight />
              </Link>
            </Button>
          ) : null
        }
      />

      {/* Activity and the funnel, one row, first: the pulse of the search
          and its totals share a glance before the work list starts. Hidden
          while filtering — they describe the whole search, not the filtered
          slice. */}
      {!filtering && (
        <div className="enter mt-4 grid grid-cols-1 gap-3 min-[1360px]:grid-cols-[minmax(0,11fr)_minmax(0,9fr)]">
          <Card className="panel rounded-xl py-4">
            <CardContent className="h-full px-4">
              <SearchPulse initial={data.calendar} />
            </CardContent>
          </Card>
          <div className="flex flex-col gap-3">
            <FunnelStrip stats={data.stats} />
            <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-3">
              <Stat
                lead
                value={`~${Math.round(data.stats.prep_hours)}h`}
                label="Prepping"
                sub={`the invisible half · ~${Math.round(data.stats.interview_hours + data.stats.prep_hours)}h total`}
                hint="Estimated hours spent preparing — the half of the work no company ever sees: 30min research per recruiter screen, 2h per phone screen, 4h of DS/algo practice per technical, 8h of system design and fundamentals (OS, DBMS, networking) per onsite, plus ~2h/week of standing practice in months with any round. An estimate anchored on confirmed rounds, not a measurement."
              />
              <Stat
                value={`~${Math.round(data.stats.interview_hours)}h`}
                label="In rooms"
                sub={`${data.stats.interviews} confirmed rounds`}
                hint="Estimated hours actually in interviews: 30min per screen, 1h per technical, 3h per onsite, over confirmed rounds only (a recruiter screen counts when your own mail near its date shows the call happened)."
              />
              <Stat
                value={
                  data.stats.weeks_to_offer === null
                    ? "—"
                    : `${data.stats.weeks_to_offer.toFixed(1)}w`
                }
                label="To an offer"
                sub="median, first contact → offer"
                hint="Median weeks from the first message ever exchanged with a company to its offer, over companies that made one with a dated offer event. The number that answers 'how long does landing one actually take'."
              />
              <Stat
                value={data.stats.rounds_30d}
                label="Rounds · 30d"
                icon={
                  data.stats.rounds_30d >= data.stats.rounds_prev_30d
                    ? TrendingUp
                    : TrendingDown
                }
                sub={`prior 30d: ${data.stats.rounds_prev_30d} · you wrote ${data.stats.outbound_30d} vs ${data.stats.outbound_prev_30d}`}
                hint="Confirmed interview rounds in the last 30 days against the 30 before, with your own outbound mail alongside — outbound is the input, rounds are the output."
              />
              <Stat
                value={pct(data.stats.ghost_rate)}
                label="Ghosted"
                accent={
                  data.stats.ghost_rate > 0.5 ? "var(--status-critical)" : undefined
                }
                hint="Of engaged applications: the conversation stopped with no reply and no rejection."
              />
              <Stat
                value={data.stats.rejections}
                label="Rejections"
                sub={`${moving} still moving`}
                hint="The company said no — as opposed to declined (you said no) or withdrawn. Alongside: live processes you actually engaged with that are still in play."
              />
            </div>
          </div>
        </div>
      )}

      {/* The filter runs over the rows already on the page — every list on
          this page came from one payload — so it answers on the keystroke
          and never asks the server anything. It sits under the header rather
          than in it because it belongs to the lists, not to the app. */}
      <Card className="panel bg-card/85 sticky top-14 z-[5] mt-4 rounded-xl py-2.5 backdrop-blur-md">
        <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3">
          <InputGroup className="h-9 min-w-64 flex-1">
            <InputGroupAddon>
              <ListFilter className="size-3.5" />
            </InputGroupAddon>
            <InputGroupInput
              ref={box}
              value={find}
              placeholder="Filter these rows — company, role, subject, stage"
              onChange={(event) => setFind(event.target.value)}
            />
            <InputGroupAddon align="inline-end">
              {filtering ? (
                <button
                  type="button"
                  onClick={() => setFind("")}
                  className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-[11px]"
                >
                  <X className="size-3" /> clear
                </button>
              ) : (
                <Kbd>/</Kbd>
              )}
            </InputGroupAddon>
          </InputGroup>

          {filtering && (
            <p className="text-muted-foreground text-[12px]">
              <b className="tabular text-foreground font-semibold">{shown}</b> of{" "}
              {held} rows match
            </p>
          )}

          {/* The filter is literal — it matches the letters on the card. When
              that finds nothing, the other search is the one that can still
              answer, and this hands it the same words. */}
          {filtering && shown === 0 && (
            <Button asChild size="xs" variant="outline">
              <Link to={`/search?q=${encodeURIComponent(find)}`}>
                <Sparkles /> Search by meaning instead
              </Link>
            </Button>
          )}
        </CardContent>
      </Card>

      <SectionHead icon={Eye} count={review.length}>
        Review
      </SectionHead>
      {review.length ? (
        <BriefingTable rows={review} mark={words} />
      ) : filtering ? (
        <EmptyState title={`No row says “${find}”`} icon={ListFilter}>
          This filter matches the words printed in a row — company, role, subject,
          stage. Nothing here carries those.
        </EmptyState>
      ) : (
        <EmptyState title="Nothing needs you" icon={Eye}>
          No unanswered inbound message in the last 120 days, and nothing is
          within a week of going cold.
        </EmptyState>
      )}

      {/* The graph, the totals and the record-quality line describe the whole
          search, not the filtered slice, so they would be reporting on a
          different set than the one on screen. They come back when the
          filter clears. */}
      {filtering ? null : (
        <>
      <SectionHead icon={ShieldCheck}>Record quality</SectionHead>
      <Card className="panel enter rounded-xl py-4">
        <CardContent className="text-muted-foreground flex flex-wrap items-center gap-x-8 gap-y-3 px-4 text-sm">
          <span>
            <b className="tabular text-foreground text-base font-semibold">
              {data.counts.pending}
            </b>{" "}
            extractions awaiting review
          </span>
          <span>
            <b className="tabular text-foreground text-base font-semibold">
              {data.counts.unverified}
            </b>{" "}
            companies never audited
          </span>
          <span>
            <b className="tabular text-foreground text-base font-semibold">
              {data.counts.flagged}
            </b>{" "}
            audits flagged
          </span>
          <Button asChild size="sm" variant="outline" className="ml-auto">
            <Link to="/triage">
              Open triage <ArrowUpRight />
            </Link>
          </Button>
        </CardContent>
      </Card>
        </>
      )}
    </div>
  )
}
