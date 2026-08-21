/**
 * The pipeline's own read of the mailbox — coverage, classification, ingest
 * health.
 *
 * This was the landing page and is still worth a glance; it just answers "how
 * is the sweep going", which is a different question from "what do I do
 * today". `ingest_run` had 589 rows and no surface at all, which is the thing
 * migration 0004 was written to prevent.
 */

import {
  Activity,
  Database,
  RadioTower,
  ScanLine,
  Users,
  Workflow,
} from "lucide-react"

import { usePipeline } from "@/lib/api"
import { fmtIso, pct } from "@/lib/record"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Dot } from "@/components/record-marks"
import {
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
  Stat,
  StatBand,
} from "@/components/page"

export function PipelinePage() {
  const { data, isPending, error } = usePipeline()
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { home_stats: home, ingest, coverage, stats } = data
  const filteredShare = home.total_messages
    ? home.negative_filtered / home.total_messages
    : 0

  return (
    <div className="space-y-2">
      <PageHead
        icon={Workflow}
        eyebrow="Sweep and coverage"
        title="Pipeline"
        lede="How much of the mailbox has been swept, by which mechanism, and whether ingestion is keeping up."
      />

      <SectionHead icon={ScanLine}>Classification</SectionHead>
      <StatBand>
        <Stat lead value={home.total_messages.toLocaleString()} label="Messages stored" />
        <Stat value={home.negative_filtered.toLocaleString()} label="Filtered free" />
        <Stat value={home.recorded.toLocaleString()} label="Resolved to a company" />
        <Stat value={home.not_job_related.toLocaleString()} label="Read, not job-related" />
        <Stat value={home.queued_for_review.toLocaleString()} label="Queued for review" />
        <Stat value={home.unclassified.toLocaleString()} label="Unclassified" />
      </StatBand>
      <p className="text-muted-foreground mt-3 text-sm">
        <span className="tabular">{pct(filteredShare)}</span> of the mailbox never reached
        a model. That is the pre-filter doing the job invariant I4 depends on — a message
        it rejects is never sent anywhere.
      </p>

      <SectionHead icon={Database}>What the pipeline has learned</SectionHead>
      <StatBand>
        <Stat value={home.companies_by_kind.employer ?? 0} label="Employers" />
        <Stat value={home.companies_by_kind.agency ?? 0} label="Agencies" />
        <Stat value={home.rules_by_source.auto ?? 0} label="Rules learned automatically" />
        <Stat value={home.rules_by_source.human ?? 0} label="Rules you taught" />
        <Stat value={home.review_queue_pending} label="Awaiting review" />
        <Stat value={stats.total_applications} label="Applications" />
      </StatBand>

      <SectionHead icon={Users}>Correspondent graph</SectionHead>
      <StatBand className="xl:grid-cols-2">
        <Stat value={coverage.with_sender ?? 0} label="Messages with a sender address" />
        <Stat value={coverage.with_thread ?? 0} label="Messages with a thread id" />
      </StatBand>

      <SectionHead icon={RadioTower}>Ingestion</SectionHead>
      <StatBand>
        <Stat value={ingest.runs} label="Runs recorded" />
        <Stat value={ingest.failures} label="Failed runs" />
        <Stat value={ingest.days_covered} label="Days covered" />
        <Stat
          value={ingest.missing_days}
          label="Days missing"
          hint="Days inside the covered span with no successful run — a backfill that quietly skipped a day looks identical to a quiet day without this."
        />
        <Stat small value={fmtIso(ingest.first_day)} label="First day" />
        <Stat small value={fmtIso(ingest.last_day)} label="Last day" />
      </StatBand>

      <SectionHead icon={Activity}>Recent runs</SectionHead>
      {ingest.recent.length ? (
        <Card className="panel enter overflow-hidden rounded-xl py-0">
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Day</TableHead>
                <TableHead className="text-right">Fetched</TableHead>
                <TableHead className="text-right">Stored</TableHead>
                <TableHead className="text-right">Inserted</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ingest.recent.map((row, index) => {
                // ingest_run, in the column order dashboard.ingest_health
                // selects: source, account, day, started_at, finished_at,
                // fetched, stored, rows_inserted, failure.
                const [source, account, day, , , fetched, stored, inserted, failure] = row
                return (
                  <TableRow key={index}>
                    <TableCell>{String(source)}</TableCell>
                    <TableCell className="text-muted-foreground">{String(account)}</TableCell>
                    {/* A run that failed before it picked a day has none —
                        String(null) would ask for the 10th of "null". */}
                    <TableCell className="tabular">
                      {fmtIso(day == null ? null : String(day))}
                    </TableCell>
                    <TableCell className="tabular text-right">{String(fetched ?? "—")}</TableCell>
                    <TableCell className="tabular text-right">{String(stored ?? "—")}</TableCell>
                    <TableCell className="tabular text-right">
                      {String(inserted ?? "—")}
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <Dot
                          color={failure ? "var(--status-critical)" : "var(--status-good)"}
                        />
                        {failure ? String(failure) : "ok"}
                      </span>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </Card>
      ) : (
        <Card className="panel rounded-xl py-6">
          <CardContent className="text-sm text-muted-foreground">
            No ingest run has been recorded yet.
          </CardContent>
        </Card>
      )}
    </div>
  )
}
