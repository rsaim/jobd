/**
 * Watch the scraper work.
 *
 * The page's one job: make a multi-minute agentic run legible — what the
 * graph is doing right now, what each query family is earning, and what the
 * record is gaining, live. The signature element is the pipeline rail: the
 * LangGraph itself drawn as a transit line, with the harvest→list return
 * edge as an arc that animates while the expansion loop is actually looping.
 * That arc IS the algorithm — everything else on the page stays quiet.
 *
 * Colour discipline follows the app's system: signal violet only on the one
 * actionable thing (Start) and the active station; volume counters use the
 * activity azure ramp; stations/stage use indigo; discoveries stay chrome.
 */

import { useEffect, useMemo, useState } from "react"
import {
  ArrowDownToLine,
  Loader2,
  Network,
  Play,
  Radar,
  ScanSearch,
  Sparkles,
} from "lucide-react"

import { Link } from "react-router-dom"

import {
  startPipeline,
  startScrape,
  usePipelineRun,
  useScrapeRuns,
  useScrapeStatus,
  type ScrapeRun,
} from "@/lib/api"
import {
  STATIONS,
  useScrapeStream,
  type ScrapeLive,
  type StationName,
} from "@/lib/scrape-stream"
import { ExpansionGraph } from "@/components/expansion-graph"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const STATION_LABEL: Record<StationName, string> = {
  plan: "Plan",
  list: "Search",
  fetch: "Fetch",
  classify: "Classify",
  audit: "Verify",
  harvest: "Learn",
  residual: "Sweep",
  report: "Report",
}

const STATION_HINT: Record<StationName, string> = {
  plan: "building seed queries",
  list: "running Gmail searches",
  fetch: "pulling new messages",
  classify: "prefilter, rules, model",
  audit: "model re-checks what was recorded",
  harvest: "positives teach new queries",
  residual: "direct mail nothing matched",
  report: "writing the run record",
}

const WINDOWS = [
  { value: "full", label: "Full backfill" },
  { value: "30", label: "Last 30 days" },
  { value: "7", label: "Last 7 days" },
  { value: "3", label: "Last 3 days" },
  { value: "1", label: "Yesterday + today" },
]

/** The daily loop — scrape, sweep, distill, exactly as the cron runs it —
 *  over the window picked in the header. One button, one optional extra
 *  (the audit, because it costs real money). Steps run as their own CLI
 *  processes, so everything lands on the Runs page with the usual metrics
 *  and credit guard. It used to be four step checkboxes and its own window
 *  select; nobody composes a custom pipeline from a dashboard, they run
 *  the loop. */
function DailyLoopCard({ days }: { days: number | null }) {
  const [withAudit, setWithAudit] = useState(false)
  const [kicking, setKicking] = useState(false)
  const status = usePipelineRun(true)
  const running = status.data?.running ?? false
  const lastError = status.data?.error
  const results = status.data?.results ?? []

  const run = async () => {
    if (days === null) return
    setKicking(true)
    try {
      const steps = ["scrape", "sweep", "distill", ...(withAudit ? ["audit"] : [])]
      await startPipeline({ steps, window_days: days })
      void status.refetch()
    } finally {
      setKicking(false)
    }
  }

  return (
    <div className="bg-card rounded-lg border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-muted-foreground font-mono text-[10px] tracking-[0.16em] uppercase">
          Daily loop
        </span>
        <span className="text-muted-foreground text-[13px]">
          scrape, sweep and distill over the window above — what the cron runs
          each morning
        </span>
        <label
          className="flex cursor-pointer items-center gap-1.5 text-[13px]"
          title="Also audit the record afterwards: merge split applications, fix links. Costs about $1."
        >
          <input
            type="checkbox"
            checked={withAudit}
            onChange={() => setWithAudit(!withAudit)}
            disabled={running}
            className="accent-primary"
          />
          also audit (~$1)
        </label>
        <Button
          size="sm"
          variant="outline"
          onClick={run}
          disabled={running || kicking || days === null}
          title={
            days === null
              ? "The loop runs over a trailing window — pick one above. A full backfill is the Start button's job."
              : undefined
          }
        >
          {running ? (
            <>
              <Loader2 className="animate-spin motion-reduce:animate-none" />
              {status.data?.step ?? "running"}
            </>
          ) : (
            <>
              <Play /> Run the loop
            </>
          )}
        </Button>
        <Link
          to="/runs"
          className="text-muted-foreground hover:text-foreground text-[13px] underline-offset-2 hover:underline"
        >
          watch on Runs →
        </Link>
      </div>
      {(lastError || results.length > 0) && !running && (
        <div className="text-muted-foreground mt-1.5 text-[12px]">
          {lastError ? (
            <span className="text-destructive">last run: {lastError}</span>
          ) : (
            <>last run: {results.map((r) => `${r.step} ${r.ok ? "ok" : "FAILED"}`).join(" · ")}</>
          )}
        </div>
      )}
    </div>
  )
}

export function ScrapePage() {
  const status = useScrapeStatus(true)
  const running = status.data?.running ?? false
  const { live, kick } = useScrapeStream(running)
  const runs = useScrapeRuns()
  const [window_, setWindow] = useState("3")
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const active = running || live.phase === "running"

  const begin = async () => {
    setStarting(true)
    try {
      // "default" resolves the server's JOBD_MODEL, then the built-in
      // default. Model choice stays a server/CLI concern.
      const result = await startScrape({
        window_days: window_ === "full" ? null : Number(window_),
        model: "default",
      })
      if (result.ok) {
        setStartError(null)
        kick()
        void status.refetch()
      } else {
        // The preflight answers the click itself (expired AWS session,
        // no accounts) — show its sentence, don't swallow it.
        setStartError(result.message ?? "Could not start the run.")
      }
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4 p-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-muted-foreground font-mono text-[10px] tracking-[0.16em] uppercase">
            Machinery · scrape, sweep, distill, audit
          </div>
          <h1 className="display text-xl font-semibold">Sync</h1>
          <p className="text-muted-foreground mt-0.5 max-w-[60ch] text-[13px]">
            Seed queries pull the high-signal mail, every confident hit teaches
            new senders to search, and a final sweep reads what nothing matched.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={window_} onValueChange={setWindow} disabled={active}>
            <SelectTrigger size="sm" className="w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((w) => (
                <SelectItem key={w.value} value={w.value}>
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" onClick={begin} disabled={active || starting}>
            {active ? (
              <>
                <Loader2 className="animate-spin motion-reduce:animate-none" />
                Running
              </>
            ) : (
              <>
                <Play /> Start
              </>
            )}
          </Button>
        </div>
      </header>

      {startError && (
        <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border px-3 py-2 text-[13px]">
          {startError}
        </div>
      )}
      {status.data?.error && (
        <div className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border px-3 py-2 text-[13px]">
          Last run failed: {status.data.error}
        </div>
      )}

      <DailyLoopCard days={window_ === "full" ? null : Number(window_)} />

      <PipelineRail live={live} active={active} />

      {live.phase === "idle" && !active ? (
        <IdleInvitation />
      ) : (
        <>
          <MetricsStrip live={live} active={active} />
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <section className="bg-card rounded-xl border p-4 shadow-xs lg:col-span-2">
              <PanelTitle icon={Network} title="Expansion graph" />
              <ExpansionGraph live={live} />
            </section>
            <Discoveries live={live} />
            <Funnel live={live} />
            <YieldFeed live={live} />
          </div>
        </>
      )}

      <RunHistory runs={runs.data?.runs ?? []} />
    </div>
  )
}

/* --------------------------------------------------------------- metrics */

function useElapsed(startedAt: string | null, running: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  if (!startedAt) return 0
  return Math.max(0, (now - new Date(startedAt).getTime()) / 1000)
}

function fmtDuration(s: number): string {
  if (s < 60) return `${Math.floor(s)}s`
  const m = Math.floor(s / 60)
  return `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(Math.round(n))
}

function MetricsStrip({ live, active }: { live: ScrapeLive; active: boolean }) {
  const c = live.counters
  const elapsed = useElapsed(live.startedAt, active)
  const shown = active ? elapsed : (c.duration_s ?? elapsed)
  const tokens = (c.prompt_tokens ?? 0) + (c.completion_tokens ?? 0)
  const tiles: { label: string; value: string; hint?: string }[] = [
    { label: "elapsed", value: fmtDuration(shown) },
    { label: "gmail calls", value: (c.api_calls ?? c.queries_run ?? 0).toLocaleString() },
    { label: "model calls", value: (c.llm_calls ?? 0).toLocaleString() },
    {
      label: "tokens",
      value: fmtTokens(tokens),
      hint: tokens ? `${fmtTokens(c.prompt_tokens ?? 0)} in · ${fmtTokens(c.completion_tokens ?? 0)} out` : undefined,
    },
    {
      label: "est. cost",
      value: `$${(c.cost_usd ?? 0).toFixed(4)}`,
      hint: c.cost_usd === 0 && (c.llm_calls ?? 0) > 0 ? "offline extractor" : undefined,
    },
    { label: "rules learned", value: (c.rules_learned ?? 0).toLocaleString() },
  ]
  return (
    <div className="grid grid-cols-3 gap-px overflow-hidden rounded-xl border sm:grid-cols-6">
      {tiles.map((tile) => (
        <div key={tile.label} className="bg-card px-3 py-2.5">
          <div className="text-muted-foreground font-mono text-[10px] tracking-[0.14em] uppercase">
            {tile.label}
          </div>
          <div className="font-mono text-lg font-semibold tabular-nums">{tile.value}</div>
          {tile.hint && (
            <div className="text-muted-foreground font-mono text-[10px] tabular-nums">
              {tile.hint}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ rail */

function PipelineRail({ live, active }: { live: ScrapeLive; active: boolean }) {
  const currentIndex = live.station ? STATIONS.indexOf(live.station) : -1
  const looping =
    active && live.hop > 0 && live.station !== "residual" && live.station !== "report"
  return (
    <div className="bg-card relative rounded-xl border px-6 pt-9 pb-4 shadow-xs">
      {/* the return edge: harvest back to list — the loop is the algorithm */}
      <svg
        className="pointer-events-none absolute inset-x-0 top-0 h-10 w-full"
        aria-hidden
      >
        <path
          d={arcPath()}
          fill="none"
          stroke="var(--chart-3)"
          strokeWidth="1.5"
          strokeDasharray="5 5"
          className={looping ? "animate-[dash_1.2s_linear_infinite] motion-reduce:animate-none" : ""}
          opacity={looping ? 0.9 : 0.3}
        />
        <style>{"@keyframes dash { to { stroke-dashoffset: -20; } }"}</style>
      </svg>
      {live.hop > 0 && (
        <div className="text-accent-foreground bg-accent absolute top-1 left-1/2 -translate-x-1/2 rounded-full px-2 py-0.5 font-mono text-[10px]">
          hop {live.hop}
        </div>
      )}
      <ol className="flex items-start justify-between gap-1">
        {STATIONS.map((name, index) => {
          const state =
            index === currentIndex && active
              ? "active"
              : index < currentIndex || live.phase === "done"
                ? "done"
                : "idle"
          return (
            <li key={name} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
              <span className="relative flex size-3 items-center justify-center">
                {state === "active" && (
                  <span className="bg-primary/40 absolute inline-flex size-full animate-ping rounded-full motion-reduce:animate-none" />
                )}
                <span
                  className={
                    state === "active"
                      ? "bg-primary ring-primary/30 relative inline-flex size-3 rounded-full ring-4"
                      : state === "done"
                        ? "relative inline-flex size-3 rounded-full bg-[var(--chart-3)]"
                        : "bg-border relative inline-flex size-3 rounded-full"
                  }
                />
              </span>
              <span
                className={
                  state === "idle"
                    ? "text-muted-foreground text-[12px]"
                    : "text-[12px] font-medium"
                }
              >
                {STATION_LABEL[name]}
              </span>
              {state === "active" && (
                <span className="text-muted-foreground max-w-full truncate font-mono text-[10px]">
                  {STATION_HINT[name]}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

/** Arc from the Learn station back to Search, in percentage-ish viewBox
 *  units — stations are evenly spaced, so their centers are fixed fractions
 *  of the width. */
function arcPath(): string {
  const n = STATIONS.length
  const learn = STATIONS.indexOf("harvest")
  const search = STATIONS.indexOf("list")
  const x = (i: number) => `${(((i + 0.5) / n) * 100).toFixed(2)}%`
  return `M ${x(learn)} 36 C ${x(learn)} 6, ${x(search)} 6, ${x(search)} 36`
}

/* ---------------------------------------------------------------- funnel */

const FUNNEL_ROWS: { key: string; label: string }[] = [
  { key: "listed", label: "Ids matched by queries" },
  { key: "listed_known", label: "Already in the record" },
  { key: "fetched", label: "Fetched" },
  { key: "classified", label: "Classified" },
  { key: "recorded", label: "Recorded" },
  { key: "queued_for_review", label: "Sent to review" },
  { key: "filtered_out", label: "Dropped by prefilter" },
  { key: "rules_learned", label: "Sender rules learned" },
]

function Funnel({ live }: { live: ScrapeLive }) {
  const max = Math.max(1, ...FUNNEL_ROWS.map((row) => live.counters[row.key] ?? 0))
  return (
    <section className="bg-card rounded-xl border p-4 shadow-xs">
      <PanelTitle icon={ArrowDownToLine} title="Funnel" />
      <div className="mt-3 flex flex-col gap-2.5">
        {FUNNEL_ROWS.map((row) => {
          const value = live.counters[row.key] ?? 0
          return (
            <div key={row.key}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px]">{row.label}</span>
                <span className="font-mono text-[13px] tabular-nums">
                  {value.toLocaleString()}
                </span>
              </div>
              <div className="bg-muted mt-1 h-1 overflow-hidden rounded-full">
                <div
                  className="h-full rounded-full bg-[var(--chart-2)] transition-[width] duration-500"
                  style={{ width: `${(value / max) * 100}%` }}
                />
              </div>
            </div>
          )
        })}
        {live.fetchProgress && live.fetchProgress.total > 0 && (
          <div className="text-muted-foreground pt-1 font-mono text-[11px] tabular-nums">
            fetching {live.fetchProgress.fetched.toLocaleString()} /{" "}
            {live.fetchProgress.total.toLocaleString()} …
          </div>
        )}
      </div>
    </section>
  )
}

/* ----------------------------------------------------------- query yield */

function originTone(origin: string): string {
  if (origin.startsWith("expand:")) return "bg-[var(--chart-3)]/12 text-[var(--chart-3)]"
  if (origin.startsWith("residual")) return "bg-[var(--chart-4)]/15 text-[var(--chart-4)]"
  return "bg-[var(--chart-2)]/12 text-[var(--chart-2)]"
}

function YieldFeed({ live }: { live: ScrapeLive }) {
  const top = useMemo(() => live.yields.slice(0, 24), [live.yields])
  const max = Math.max(1, ...top.map((y) => y.matched))
  return (
    <section className="bg-card rounded-xl border p-4 shadow-xs">
      <PanelTitle icon={ScanSearch} title="Query yield" />
      <div className="mt-3 flex flex-col gap-1.5">
        {top.length === 0 && (
          <p className="text-muted-foreground text-[13px]">
            Searches report here as they run.
          </p>
        )}
        {top.map((y) => (
          <div
            key={y.seq}
            className="animate-in fade-in slide-in-from-top-1 duration-300 motion-reduce:animate-none"
            title={y.q}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span
                className={`truncate rounded px-1.5 py-0.5 font-mono text-[10px] ${originTone(y.origin)}`}
              >
                {y.origin}
              </span>
              <span className="text-muted-foreground shrink-0 font-mono text-[11px] tabular-nums">
                <span className="text-foreground font-medium">+{y.fresh}</span>
                {" / "}
                {y.matched}
              </span>
            </div>
            <div className="bg-muted mt-0.5 h-0.5 overflow-hidden rounded-full">
              <div
                className="h-full bg-[var(--chart-2)]/60"
                style={{ width: `${(y.matched / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

/* ----------------------------------------------------------- discoveries */

function Discoveries({ live }: { live: ScrapeLive }) {
  return (
    <section className="bg-card rounded-xl border p-4 shadow-xs">
      <PanelTitle icon={Sparkles} title="Discoveries" />
      <div className="mt-3 flex flex-col gap-2">
        {live.facts.length === 0 && (
          <p className="text-muted-foreground text-[13px]">
            Companies, stage moves and lessons the run finds appear here.
          </p>
        )}
        {live.facts.map((fact) => (
          <div
            key={fact.seq}
            className="animate-in fade-in slide-in-from-top-1 border-l-2 border-[var(--chart-3)]/50 pl-2.5 text-[13px] duration-300 motion-reduce:animate-none"
          >
            {fact.text}
          </div>
        ))}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ idle */

function IdleInvitation() {
  return (
    <div className="bg-card flex flex-col items-center gap-3 rounded-xl border px-6 py-12 text-center shadow-xs">
      <div className="bg-accent text-accent-foreground flex size-10 items-center justify-center rounded-full">
        <Radar className="size-5" />
      </div>
      <p className="display text-[15px] font-medium">The mailbox is quiet.</p>
      <p className="text-muted-foreground max-w-[52ch] text-[13px]">
        Start a scrape to hunt for job mail. A daily window re-checks recent
        mail and every sender the record has learned; a full backfill walks the
        whole strategy from seeds. Both are safe to re-run — nothing is fetched
        twice.
      </p>
    </div>
  )
}

/* --------------------------------------------------------------- history */

function RunHistory({ runs }: { runs: ScrapeRun[] }) {
  if (runs.length === 0) return null
  return (
    <section className="bg-card rounded-xl border p-4 shadow-xs">
      <PanelTitle icon={Radar} title="Previous runs" />
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-muted-foreground border-b text-left font-mono text-[10px] tracking-[0.12em] uppercase">
              <th className="py-1.5 pr-3 font-medium">Started</th>
              <th className="py-1.5 pr-3 font-medium">Window</th>
              <th className="py-1.5 pr-3 font-medium">Accounts</th>
              <th className="py-1.5 pr-3 text-right font-medium">Fetched</th>
              <th className="py-1.5 pr-3 text-right font-medium">Recorded</th>
              <th className="py-1.5 pr-3 text-right font-medium">Review</th>
              <th className="py-1.5 text-right font-medium">Model calls</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-b last:border-0">
                <td className="py-1.5 pr-3 font-mono text-[12px] tabular-nums">
                  {run.started_at?.slice(0, 16).replace("T", " ")}
                </td>
                <td className="py-1.5 pr-3">
                  {run.window_days ? `${run.window_days}d` : "full"}
                </td>
                <td className="text-muted-foreground max-w-[26ch] truncate py-1.5 pr-3">
                  {run.accounts.join(", ")}
                </td>
                <Num value={run.counters.fetched} />
                <Num value={run.counters.recorded} />
                <Num value={run.counters.queued_for_review} />
                <Num value={run.counters.llm_calls} last />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Num({ value, last }: { value: number | undefined; last?: boolean }) {
  return (
    <td className={`py-1.5 text-right font-mono text-[12px] tabular-nums ${last ? "" : "pr-3"}`}>
      {(value ?? 0).toLocaleString()}
    </td>
  )
}

/* ------------------------------------------------------------------ bits */

function PanelTitle({
  icon: Icon,
  title,
}: {
  icon: typeof Sparkles
  title: string
}) {
  return (
    <div className="flex items-center gap-1.5">
      <Icon className="text-muted-foreground size-3.5" />
      <h2 className="text-muted-foreground font-mono text-[10px] tracking-[0.16em] uppercase">
        {title}
      </h2>
    </div>
  )
}
