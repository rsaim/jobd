/**
 * The decision workbench: the queue, the senders, the rules, the audits.
 *
 * Every decision the CLI could make and the dashboard could not, in the one
 * place where the evidence for making it is already on screen.
 *
 * The queue is two panes — the list on the left, the selected item open
 * beside it — and both scroll inside the viewport rather than growing the
 * page. A 50-item queue beside a 20,000-character message otherwise makes a
 * 4,000px page where neither half is usable.
 */

import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import {
  BookCheck,
  Check,
  ChevronLeft,
  ChevronRight,
  Filter,
  Inbox,
  ListChecks,
  ScanLine,
  Sparkles,
  Trash2,
  Users,
  X,
} from "lucide-react"

import { useTriage, useWrite, qs } from "@/lib/api"
import { fmtIso } from "@/lib/record"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { MessageBody } from "@/components/message-row"
import { StageBadge, Tag } from "@/components/record-marks"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
  Stat,
  StatBand,
} from "@/components/page"
import { useState } from "react"

const TABS = [
  { value: "queue", label: "Review queue", count: "pending", icon: Inbox },
  { value: "senders", label: "Senders", count: null, icon: Users },
  { value: "rules", label: "Rules", count: "rules", icon: ListChecks },
  { value: "audits", label: "Audits", count: "flagged", icon: BookCheck },
] as const

export function TriagePage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") ?? "queue"
  const query = qs({
    tab,
    reason: params.get("reason") ?? "",
    item: params.get("item") ?? "",
    source: params.get("source") ?? "",
    verdict: params.get("verdict") ?? "",
    flagged: params.get("flagged") ?? "",
    page: params.get("page") ?? "",
  })
  const { data, isPending, error } = useTriage(query)

  const set = (entries: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(entries)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-2">
      <PageHead
        icon={Filter}
        eyebrow="Decisions only you can make"
        title="Triage"
        lede="Every decision the CLI could make and the dashboard could not, in the place where the evidence for making it is already open."
      />

      <Tabs
        value={tab}
        onValueChange={(value) => setParams({ tab: value }, { replace: true })}
        className="mt-5"
      >
        {/* Four labelled tabs are wider than a phone; the strip scrolls
            rather than the page. */}
        <TabsList className="h-auto max-w-full overflow-x-auto [&>button]:min-h-9 sm:[&>button]:min-h-0">
          {TABS.map((entry) => (
            <TabsTrigger key={entry.value} value={entry.value} className="gap-2">
              <entry.icon className="size-3.5" />
              {entry.label}
              {entry.count && data?.counts[entry.count] ? (
                <span className="tabular bg-muted rounded-full px-1.5 text-[10.5px] leading-4">
                  {data.counts[entry.count]}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {isPending ? (
        <Loading />
      ) : error ? (
        <ErrorState error={error} />
      ) : tab === "queue" ? (
        <QueueTab data={data} params={params} set={set} />
      ) : tab === "senders" ? (
        <SendersTab data={data} />
      ) : tab === "rules" ? (
        <RulesTab data={data} params={params} set={set} />
      ) : (
        <AuditsTab data={data} params={params} set={set} />
      )}
    </div>
  )
}

type Payload = NonNullable<ReturnType<typeof useTriage>["data"]>
type Setter = (entries: Record<string, string>) => void

function QueueTab({
  data,
  params,
  set,
}: {
  data: Payload
  params: URLSearchParams
  set: Setter
}) {
  const items = data.items ?? []
  const reason = params.get("reason") ?? ""
  const itemId = params.get("item")
  const current = items.find((row) => row.id === itemId) ?? items[0]
  const resolve = useWrite<{ review_id: string; decision: string }>(
    () => "/review/resolve",
    ["triage", "chrome", "home"],
  )
  const bulk = useWrite<{ reason: string; decision: string }>(
    () => "/review/bulk",
    ["triage", "chrome", "home"],
  )

  return (
    <div className="mt-4 space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">
        Extractions the model was not confident enough to record. Nothing here has
        touched the record — approving marks the decision, it does not write the
        extraction, because the write path in{" "}
        <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">classify</code> is
        the one under test.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={reason ? "ghost" : "secondary"}
          size="sm"
          onClick={() => set({ reason: "", item: "", page: "" })}
        >
          All reasons
        </Button>
        {(data.reasons ?? []).map(([text, count]) => (
          <Button
            key={text}
            variant={reason === text ? "secondary" : "ghost"}
            size="sm"
            onClick={() => set({ reason: text, item: "", page: "" })}
          >
            {text} <span className="tabular ml-1 text-muted-foreground">{count}</span>
          </Button>
        ))}
        {reason && (
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto"
            onClick={() => {
              if (!window.confirm(`Reject every open item with reason: ${reason}?`)) return
              bulk.mutate(
                { reason, decision: "rejected" },
                { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
              )
            }}
          >
            <Trash2 /> Reject the whole bucket
          </Button>
        )}
      </div>

      {items.length === 0 ? (
        <EmptyState title="Queue empty" icon={Check}>
          Nothing is waiting on a decision{reason ? " for that reason" : ""}.
        </EmptyState>
      ) : (
        <>
        <QueuePager data={data} params={params} set={set} shown={items.length} />
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(320px,400px)_minmax(0,1fr)]">
          <Card className="panel max-h-[calc(100dvh-19rem)] overflow-y-auto rounded-xl py-0 xl:sticky xl:top-20">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => set({ item: item.id })}
                className={cn(
                  "hover:bg-muted/60 flex w-full items-center gap-3 border-b p-3 text-left transition-colors last:border-b-0",
                  item.id === current?.id &&
                    "bg-muted shadow-[inset_2px_0_0_0_var(--primary)]",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium">
                    {(item.extraction.company_name as string) || "(no company)"}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {item.subject || "(no subject)"}
                  </span>
                </span>
                {/* Label and stage together: the label says what kind of
                    message the extractor thought this was, the stage says
                    what it claimed happened. Reviewing is checking those two
                    against the subject, so both belong on the row being
                    scanned rather than only in the detail panel. */}
                {(item.extraction.stage as string | null) && (
                  <StageBadge stage={item.extraction.stage as string} />
                )}
                <Tag>{(item.extraction.label as string) || "—"}</Tag>
              </button>
            ))}
          </Card>

          {current && (
            <div className="space-y-4">
              <Card className="panel rounded-xl py-4">
                <CardHeader className="px-4">
                  <CardTitle className="display text-base">
                    {(current.extraction.company_name as string) || "No company identified"}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-4">
                  <p className="mb-3 text-xs text-muted-foreground">
                    {current.reason} · {current.extracted_by} · queued{" "}
                    {fmtIso(current.created_at)}
                  </p>
                  {/* Only the fields the extractor actually filled. Printing
                      every null as an em-dash buries the two or three values
                      that carry the claim. */}
                  <dl className="bg-muted/60 grid gap-1 rounded-lg p-3 font-mono text-[11.5px] break-words">
                    {Object.entries(current.extraction)
                      .filter(([, value]) => value !== null && value !== "")
                      .map(([key, value]) => (
                        <div key={key}>
                          {key}&nbsp;&nbsp;{String(value)}
                        </div>
                      ))}
                    {Object.values(current.extraction).every((v) => v === null || v === "") && (
                      <div>The extractor returned no fields at all.</div>
                    )}
                  </dl>
                  <div className="mt-4 flex gap-2">
                    <Button
                      size="sm"
                      onClick={() =>
                        resolve.mutate(
                          { review_id: current.id, decision: "approved" },
                          { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                        )
                      }
                    >
                      <Check /> Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() =>
                        resolve.mutate(
                          { review_id: current.id, decision: "rejected" },
                          { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                        )
                      }
                    >
                      <X /> Reject
                    </Button>
                  </div>
                </CardContent>
              </Card>

              {data.selected && (
                <Card className="panel rounded-xl py-4">
                  <CardHeader className="px-4">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <ScanLine className="text-muted-foreground size-4" /> Evidence
                    </CardTitle>
                    <p className="text-[12.5px] text-muted-foreground">
                      {data.selected.subject || "(no subject)"}
                    </p>
                  </CardHeader>
                  <CardContent className="px-4">
                    <MessageBody id={data.selected.id} dense />
                  </CardContent>
                </Card>
              )}
            </div>
          )}
        </div>
        </>
      )}
    </div>
  )
}

/** "48 shown, 1,716 total" plus Next/Previous — real bug, live-caught: the
 *  queue paginates server-side but nothing before this ever wrote a new
 *  `page` back to the URL, so item 51 onward was permanently unreachable. */
function QueuePager({
  data,
  params,
  set,
  shown,
}: {
  data: Payload
  params: URLSearchParams
  set: Setter
  shown: number
}) {
  const page = Math.max(Number(params.get("page")) || 1, 1)
  const pageSize = data.page_size ?? shown
  const total = data.total ?? shown
  const start = (page - 1) * pageSize + 1
  const end = start + shown - 1
  const hasPrev = page > 1
  const hasNext = end < total

  if (!hasPrev && !hasNext) return null

  return (
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span className="tabular">
        {start}–{end} of {total}
      </span>
      <div className="flex gap-1">
        <Button
          size="xs"
          variant="outline"
          disabled={!hasPrev}
          onClick={() => set({ page: String(page - 1) })}
        >
          <ChevronLeft /> Previous
        </Button>
        <Button
          size="xs"
          variant="outline"
          disabled={!hasNext}
          onClick={() => set({ page: String(page + 1) })}
        >
          Next <ChevronRight />
        </Button>
      </div>
    </div>
  )
}

function TeachForm() {
  const [form, setForm] = useState({
    domain: "",
    address: "",
    verdict: "negative",
    company: "",
  })
  const teach = useWrite<typeof form>(() => "/teach", ["triage", "chrome", "messages", "home"])
  return (
    <Card className="panel rounded-xl py-4">
      <CardContent className="flex flex-wrap items-center gap-2 px-4">
        <Input
          value={form.domain}
          onChange={(e) => setForm({ ...form, domain: e.target.value })}
          placeholder="Domain, e.g. acme.com"
          className="h-8 w-52 text-xs"
        />
        <span className="text-xs text-muted-foreground">or</span>
        <Input
          value={form.address}
          onChange={(e) => setForm({ ...form, address: e.target.value })}
          placeholder="Address"
          className="h-8 w-56 text-xs"
        />
        <Select value={form.verdict} onValueChange={(v) => setForm({ ...form, verdict: v })}>
          <SelectTrigger size="sm" className="w-44 text-xs" aria-label="Verdict">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="negative">Not job-related</SelectItem>
            <SelectItem value="positive">Job-related</SelectItem>
            <SelectItem value="undecided">Needs the model</SelectItem>
          </SelectContent>
        </Select>
        <Input
          value={form.company}
          onChange={(e) => setForm({ ...form, company: e.target.value })}
          placeholder="Company (positive only)"
          className="h-8 w-52 text-xs"
        />
        <Button
          size="sm"
          disabled={teach.isPending}
          onClick={() =>
            teach.mutate(form, {
              onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message),
            })
          }
        >
          Teach
        </Button>
        <p className="w-full text-[11.5px] text-muted-foreground">
          A rule applies to every message ingested from now on, and sweeps the
          unclassified backlog immediately. It never overwrites a message that has
          already been classified.
        </p>
      </CardContent>
    </Card>
  )
}

function SendersTab({ data }: { data: Payload }) {
  const coverage = data.coverage ?? {}
  const teach = useWrite<{ domain?: string; address?: string; verdict: string }>(
    () => "/teach",
    ["triage", "chrome", "messages"],
  )
  return (
    <div className="mt-4 space-y-4">
      {coverage.with_sender === 0 && (
        <Alert variant="destructive">
          <AlertTitle>The correspondent graph is empty</AlertTitle>
          <AlertDescription>
            0 of {coverage.total} messages carry a sender address. Migration 0007 added
            those columns and ingestion fills them from the parsed envelope, but every
            message here was stored before that — so fanout, sender ranking and the rules
            below match nothing until <code>jobd rebuild</code> re-derives them from raw
            storage.
          </AlertDescription>
        </Alert>
      )}

      <StatBand>
        <Stat lead value={coverage.total ?? 0} label="Messages" />
        <Stat value={coverage.with_sender ?? 0} label="With a sender address" />
        <Stat value={coverage.with_thread ?? 0} label="With a thread id" />
        <Stat value={coverage.unclassified ?? 0} label="Unclassified backlog" />
      </StatBand>

      <SectionHead icon={Sparkles}>Teach a rule</SectionHead>
      <TeachForm />

      <SectionHead icon={Users}>Highest-leverage unresolved senders</SectionHead>
      {data.unresolved?.length ? (
        <Card className="panel overflow-hidden rounded-xl py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-right">Messages</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Sender</TableHead>
                <TableHead>Example subject</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.unresolved.map((row) => (
                <TableRow key={`${row.kind}-${row.key}`}>
                  <TableCell className="tabular text-right">{row.count}</TableCell>
                  <TableCell className="text-muted-foreground">{row.kind}</TableCell>
                  <TableCell>
                    <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                      {row.key}
                    </code>
                  </TableCell>
                  <TableCell className="max-w-96 truncate text-muted-foreground">
                    {row.sample_subject}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() =>
                        teach.mutate(
                          {
                            [row.kind === "domain" ? "domain" : "address"]: row.key,
                            verdict: "negative",
                          } as { domain?: string; address?: string; verdict: string },
                          { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                        )
                      }
                    >
                      Not job-related
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      ) : (
        <EmptyState title="Nothing to rank" icon={Users}>
          Every message has been classified, so there is no backlog to resolve.
        </EmptyState>
      )}
    </div>
  )
}

function VerdictSelect({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" className="w-40 text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="positive">Job-related</SelectItem>
        <SelectItem value="negative">Not job-related</SelectItem>
        <SelectItem value="undecided">Needs the model</SelectItem>
      </SelectContent>
    </Select>
  )
}

function RulesTab({
  data,
  params,
  set,
}: {
  data: Payload
  params: URLSearchParams
  set: Setter
}) {
  const setCategory = useWrite<{ category: string; verdict: string }>(
    () => "/category/verdict",
    ["triage", "messages"],
  )
  /* Reuses /teach — the exact endpoint the "Teach a rule" form and the CLI's
     `jobd learn` both call, not a second write path. Re-teaching the same
     match with a new verdict is the established upsert. */
  const reteach = useWrite<{
    domain?: string
    address?: string
    verdict: string
    company?: string
  }>(() => "/teach", ["triage", "messages"])

  return (
    <div className="mt-4 space-y-4">
      <SectionHead icon={ListChecks}>Sender groups</SectionHead>
      <p className="text-sm text-muted-foreground">
        Changing a group's verdict re-decides every sender tagged with it, not just one.
      </p>
      <Card className="panel overflow-hidden rounded-xl py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Group</TableHead>
              <TableHead className="text-right">Senders</TableHead>
              <TableHead>Company</TableHead>
              <TableHead>Verdict</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data.categories ?? []).map((row) => (
              <TableRow key={row.category}>
                <TableCell>
                  <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                    {row.category}
                  </code>
                </TableCell>
                <TableCell className="tabular text-right">{row.rule_count}</TableCell>
                <TableCell className="text-muted-foreground">
                  {row.company_name || "—"}
                </TableCell>
                <TableCell>
                  <VerdictSelect
                    value={row.verdict}
                    onChange={(verdict) =>
                      setCategory.mutate(
                        { category: row.category, verdict },
                        { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                      )
                    }
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <SectionHead icon={ListChecks} count={data.rules?.length}>Rules</SectionHead>
      <div className="flex flex-wrap gap-2">
        <Select
          value={params.get("source") || "any"}
          onValueChange={(v) => set({ source: v === "any" ? "" : v })}
        >
          <SelectTrigger size="sm" className="w-48 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Taught by anyone</SelectItem>
            <SelectItem value="human">Taught by you</SelectItem>
            <SelectItem value="auto">Learned automatically</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={params.get("verdict") || "any"}
          onValueChange={(v) => set({ verdict: v === "any" ? "" : v })}
        >
          <SelectTrigger size="sm" className="w-44 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any verdict</SelectItem>
            <SelectItem value="positive">Job-related</SelectItem>
            <SelectItem value="negative">Not job-related</SelectItem>
            <SelectItem value="undecided">Needs the model</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Card className="panel overflow-hidden rounded-xl py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Match</TableHead>
              <TableHead>Verdict</TableHead>
              <TableHead>Group</TableHead>
              <TableHead>Company</TableHead>
              <TableHead className="text-right">Learned</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data.rules ?? []).map((rule) => (
              <TableRow key={rule.id}>
                {/* Match type and who taught it ride with the match rather
                    than as columns of their own: six columns did not fit
                    beside the chat panel and pushed Learned off the card. */}
                <TableCell>
                  <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                    {rule.value}
                  </code>
                  <Tag className="ml-2">
                    {rule.match_type} · {rule.source}
                  </Tag>
                </TableCell>
                <TableCell>
                  <VerdictSelect
                    value={rule.verdict}
                    onChange={(verdict) =>
                      reteach.mutate(
                        {
                          [rule.match_type === "domain" ? "domain" : "address"]: rule.value,
                          verdict,
                          ...(rule.company_name ? { company: rule.company_name } : {}),
                        } as {
                          domain?: string
                          address?: string
                          verdict: string
                          company?: string
                        },
                        { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                      )
                    }
                  />
                </TableCell>
                <TableCell className="text-muted-foreground">{rule.category || "—"}</TableCell>
                <TableCell>
                  {rule.company_id ? (
                    <Link
                      to={`/company/${rule.company_id}`}
                      className="underline underline-offset-4"
                    >
                      {rule.company_name}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="tabular text-right text-muted-foreground">
                  {fmtIso(rule.created_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  )
}

function AuditsTab({
  data,
  params,
  set,
}: {
  data: Payload
  params: URLSearchParams
  set: Setter
}) {
  const flagged = params.get("flagged") === "1"
  const apply = useWrite<{
    match_type: string
    value: string
    verdict: string
    company_name: string
  }>(() => "/verification/apply", ["triage", "companies"])

  return (
    <div className="mt-4 space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">
        Whole-chain audit passes. A finding, never a mutation — nothing here changed the
        record, and an audit of a company that was later removed keeps its own reasoning
        so the decision stays explainable.
      </p>
      <div className="flex gap-2">
        <Button
          variant={flagged ? "ghost" : "secondary"}
          size="sm"
          onClick={() => set({ flagged: "" })}
        >
          All <span className="tabular ml-1">{data.counts.verifications}</span>
        </Button>
        <Button
          variant={flagged ? "secondary" : "ghost"}
          size="sm"
          onClick={() => set({ flagged: "1" })}
        >
          Flagged <span className="tabular ml-1">{data.counts.flagged}</span>
        </Button>
      </div>

      {data.verifications?.length ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {data.verifications.map((audit) => (
            <Card key={audit.id} className="panel rounded-xl py-4">
              <CardHeader className="px-4">
                <CardTitle className="flex items-center justify-between gap-2 text-sm">
                  {audit.company_id ? (
                    <Link
                      to={`/company/${audit.company_id}`}
                      className="underline underline-offset-4"
                    >
                      {audit.company_name}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">
                      {audit.verified_name || "(removed)"}
                    </span>
                  )}
                  <Badge variant={audit.classification_correct ? "secondary" : "destructive"}>
                    {audit.classification_correct ? "confirmed" : "flagged"}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-4 text-[12.5px]">
                <p className="mb-2 text-[11.5px] text-muted-foreground">
                  {audit.model} · {audit.message_count} message(s) ·{" "}
                  {fmtIso(audit.created_at)}
                  {!audit.company_id ? " · company removed after this audit" : ""}
                </p>
                {audit.reasoning && <p className="mb-2">{audit.reasoning}</p>}
                {audit.suggested_rules.map((rule, index) => (
                  <div key={index} className="mb-2 flex flex-wrap items-center gap-2">
                    <Badge variant={rule.verdict === "negative" ? "destructive" : "secondary"}>
                      {rule.verdict}
                    </Badge>
                    <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                      {rule.value}
                    </code>
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() =>
                        apply.mutate(
                          {
                            match_type: rule.match_type,
                            value: rule.value,
                            verdict: rule.verdict,
                            company_name: audit.verified_name || audit.company_name || "",
                          },
                          { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                        )
                      }
                    >
                      Apply
                    </Button>
                    {rule.reason && (
                      <span className="w-full text-[11px] text-muted-foreground">
                        {rule.reason}
                      </span>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState title="No audits" icon={BookCheck}>
          Run <code>jobd verify</code> to audit companies' whole message chains.
        </EmptyState>
      )}
    </div>
  )
}
