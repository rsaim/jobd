/**
 * Live pipeline runs — the machinery's own heartbeat.
 *
 * Every long job (classify workers, review sweep, audit, envelope backfill)
 * keeps one `pipeline_run` row current as it works; this page polls that
 * table every 2s while anything is live. The signature element is the
 * progress spine per active run: one bar whose fill is the processed
 * fraction, with rate, ETA and spend read off the same row — so a
 * fifty-eight-thousand-message rerun and a two-minute sweep are legible the
 * same way. Stall detection is the quiet safety feature: a live writer
 * flushes every ~2s, so twenty silent seconds paints the run amber before a
 * human would otherwise notice a hung call.
 */

import { Gauge, OctagonAlert, Timer } from "lucide-react"

import { useRuns, type PipelineRun } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
} from "@/components/page"

const fmtDuration = (seconds: number | null | undefined): string => {
  if (seconds == null) return "—"
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

/** "today 14:32", "yesterday 09:10", then "Aug 18, 09:10" — a run list is
 *  read for recency first, so the day collapses to a word while it can. The
 *  cell's tooltip keeps the full timestamp. */
const fmtStarted = (iso: string): string => {
  const d = new Date(iso)
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, today)) return `today ${time}`
  if (sameDay(d, yesterday)) return `yesterday ${time}`
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  return `${day}, ${time}`
}

const fmtTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${Math.round(n / 1_000)}k` : String(n)

const KIND_LABEL: Record<string, string> = {
  classify: "Classify",
  sweep: "Review sweep",
  audit: "Audit",
  backfill: "Envelope backfill",
}

function statusTone(run: PipelineRun): string {
  if (run.stalled) return "text-amber-600 dark:text-amber-500"
  if (run.status === "failed") return "text-red-600 dark:text-red-500"
  if (run.finished_at) return "text-muted-foreground"
  return "text-emerald-600 dark:text-emerald-500"
}

function ActiveRun({ run }: { run: PipelineRun }) {
  const fraction = run.total ? Math.min(run.processed / run.total, 1) : null
  const phase = run.counters.phase as string | undefined
  const counters = Object.entries(run.counters)
    .filter(([k, v]) => k !== "phase" && typeof v === "number" && v > 0)
    .sort(([, a], [, b]) => (b as number) - (a as number))
  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className="font-medium">{KIND_LABEL[run.kind] ?? run.kind}</span>
            {run.worker && (
              <span className="text-muted-foreground text-xs">worker {run.worker}</span>
            )}
            {phase && (
              <span className="text-muted-foreground text-xs">· {phase}</span>
            )}
          </div>
          <span className={`text-xs font-medium ${statusTone(run)}`}>
            {run.stalled ? "stalled — no heartbeat for 20s+" : "running"}
          </span>
        </div>

        <div>
          <div className="bg-muted h-2 w-full overflow-hidden rounded-full">
            <div
              className={`h-full rounded-full transition-[width] duration-700 ${
                run.stalled ? "bg-amber-500" : "bg-primary"
              }`}
              style={{ width: fraction != null ? `${Math.max(fraction * 100, 1)}%` : "100%" }}
            />
          </div>
          <div className="text-muted-foreground mt-1 flex flex-wrap justify-between gap-x-4 text-xs tabular-nums">
            <span>
              {run.processed.toLocaleString()}
              {run.total ? ` / ${run.total.toLocaleString()}` : ""} items
            </span>
            <span>
              {run.rate_per_min > 0 && `${run.rate_per_min.toLocaleString()}/min`}
              {run.eta_s != null && ` · ~${fmtDuration(run.eta_s)} left`}
              {` · ${fmtDuration(run.elapsed_s)} elapsed`}
            </span>
          </div>
        </div>

        <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
          <span>${run.llm_cost_usd.toFixed(2)} spent</span>
          <span>{run.llm_calls.toLocaleString()} model calls</span>
          <span>
            {fmtTokens(run.llm_tokens_in)} in / {fmtTokens(run.llm_tokens_out)} out
          </span>
          {run.errors > 0 && (
            <span className="text-red-600 dark:text-red-500">{run.errors} errors</span>
          )}
        </div>

        {counters.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {counters.map(([key, value]) => (
              <span
                key={key}
                className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[11px] tabular-nums"
              >
                {key.replaceAll("_", " ")} {(value as number).toLocaleString()}
              </span>
            ))}
          </div>
        )}

        {run.last_error && (
          <p className="text-muted-foreground flex items-start gap-1.5 text-xs">
            <OctagonAlert className="mt-0.5 size-3.5 shrink-0 text-red-500" />
            <span className="break-all">{run.last_error}</span>
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function RunsPage() {
  const { data, isPending, error } = useRuns()
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const active = data.runs.filter((r) => !r.finished_at)
  const done = data.runs.filter((r) => r.finished_at)

  return (
    <div className="space-y-2">
      <PageHead
        icon={Gauge}
        eyebrow="Machinery"
        title="Runs"
        lede="Every long job reports its own progress here while it works — items, rate, time remaining, model spend — and goes amber the moment its heartbeat stops."
      />

      {active.length > 0 && (
        <>
          <SectionHead icon={Timer}>Running now</SectionHead>
          <div className="space-y-2">
            {active.map((run) => (
              <ActiveRun key={run.id} run={run} />
            ))}
          </div>
        </>
      )}
      {active.length === 0 && (
        <EmptyState icon={Timer} title="Nothing running">
          Start a classify, sweep, audit or backfill from the CLI and it appears
          here within two seconds.
        </EmptyState>
      )}

      {done.length > 0 && (
        <>
          <SectionHead icon={Gauge}>Recent runs</SectionHead>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Started</TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Items</TableHead>
                <TableHead className="text-right">Took</TableHead>
                <TableHead className="text-right">Rate/min</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Spend</TableHead>
                <TableHead className="text-right">Errors</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {done.map((run) => (
                <TableRow key={run.id}>
                  <TableCell
                    className="text-muted-foreground whitespace-nowrap tabular-nums"
                    title={new Date(run.started_at).toLocaleString()}
                  >
                    {fmtStarted(run.started_at)}
                  </TableCell>
                  <TableCell>
                    {KIND_LABEL[run.kind] ?? run.kind}
                    {run.worker ? ` ${run.worker}` : ""}
                  </TableCell>
                  <TableCell className={statusTone(run)}>{run.status}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {run.processed.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmtDuration(run.elapsed_s)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {run.rate_per_min.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {run.llm_calls.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    ${run.llm_cost_usd.toFixed(2)}
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${run.errors ? "text-red-600 dark:text-red-500" : ""}`}
                  >
                    {run.errors}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  )
}
