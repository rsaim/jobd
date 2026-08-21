/**
 * Every employer and agency the pipeline discovered.
 *
 * Never user-created: this list is what the mail said, not what anyone typed.
 *
 * Every filter is a query parameter and the table reads its state from the
 * URL rather than from component state, so gate 1 — paste the URL in a fresh
 * session and get the identical view — holds without anything having to
 * serialise a store.
 */

import { Link, useSearchParams } from "react-router-dom"
import { Building2, Search, X } from "lucide-react"

import { useCompanies, qs } from "@/lib/api"
import { fmtIso, pct } from "@/lib/record"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
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
import { CompanyMark } from "@/components/company-mark"
import { KindBadge, StageBadge, TurnBadge } from "@/components/record-marks"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  Stat,
  StatBand,
} from "@/components/page"

const ANY = "any"

export function CompaniesPage() {
  const [params, setParams] = useSearchParams()
  const search = params.get("search") ?? ""
  const kind = params.get("kind") ?? ""
  const turn = params.get("turn") ?? ""
  const sort = params.get("sort") ?? "recency"
  const active = params.get("active") ?? ""
  const substantive = params.get("substantive") ?? ""

  const query = qs({ search, kind, turn, sort, active, substantive })
  const { data, isPending, error } = useCompanies(query)
  const filtered = Boolean(search || kind || turn || active || substantive)

  /** One writer for the address bar: every control edits the URL, and the
   *  table re-reads it. Nothing holds a second copy of the filter state. */
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value && value !== ANY) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-2">
      <PageHead
        icon={Building2}
        eyebrow="Discovered by the pipeline"
        title="Companies"
        lede="Every employer and agency the pipeline discovered. Never user-created — this list is what the mail said, not what you typed."
      />

      {data && (
        <StatBand>
          <Stat lead value={data.companies.length} label="Shown" />
          <Stat value={data.stats.total_applications} label="Applications" />
          <Stat value={pct(data.stats.response_rate)} label="Replied" />
          <Stat value={pct(data.stats.ghost_rate)} label="Ghosted" />
          <Stat value={data.stats.interviews} label="Interviews" />
          <Stat value={data.stats.offers} label="Offers" />
        </StatBand>
      )}

      {/* The filter bar sticks: with 577 rows the controls are otherwise a
          scroll back to the top away, and this page is used by narrowing. */}
      <Card className="panel bg-card/85 sticky top-14 z-[5] mt-6 rounded-xl py-3 backdrop-blur-md">
        <CardContent className="flex flex-wrap items-center gap-2 px-3">
          <InputGroup className="h-9 w-64">
            <InputGroupAddon>
              <Search className="size-3.5" />
            </InputGroupAddon>
            <InputGroupInput
              defaultValue={search}
              placeholder="Company or domain"
              onKeyDown={(event) => {
                if (event.key === "Enter") set("search", event.currentTarget.value)
              }}
              onBlur={(event) => set("search", event.currentTarget.value)}
            />
          </InputGroup>
          <Select value={kind || ANY} onValueChange={(value) => set("kind", value)}>
            <SelectTrigger className="w-36" aria-label="Kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any kind</SelectItem>
              <SelectItem value="employer">Employer</SelectItem>
              <SelectItem value="agency">Agency</SelectItem>
            </SelectContent>
          </Select>
          <Select value={turn || ANY} onValueChange={(value) => set("turn", value)}>
            <SelectTrigger className="w-40" aria-label="Turn">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any turn</SelectItem>
              <SelectItem value="your_turn">Your turn</SelectItem>
              <SelectItem value="their_turn">Their turn</SelectItem>
            </SelectContent>
          </Select>
          <Select value={active || ANY} onValueChange={(value) => set("active", value)}>
            <SelectTrigger className="w-40" aria-label="Active within">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any time</SelectItem>
              <SelectItem value="30">Active in 30 days</SelectItem>
              <SelectItem value="90">Active in 90 days</SelectItem>
              <SelectItem value="365">Active in a year</SelectItem>
            </SelectContent>
          </Select>
          <Select value={sort} onValueChange={(value) => set("sort", value)}>
            <SelectTrigger className="w-44" aria-label="Sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recency">Recent activity</SelectItem>
              <SelectItem value="messages">Most messages</SelectItem>
              <SelectItem value="applications">Most applications</SelectItem>
              <SelectItem value="name">Name</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant={substantive === "1" ? "default" : "outline"}
            size="sm"
            onClick={() => set("substantive", substantive === "1" ? "" : "1")}
            title="Hide companies with a single message and no application"
          >
            Substantive only
          </Button>
          {filtered && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
            >
              <X /> Clear
            </Button>
          )}
          {data && (
            <span className="text-muted-foreground ml-auto text-xs">
              <span className="tabular text-foreground font-medium">
                {data.companies.length}
              </span>{" "}
              of <span className="tabular">{data.total}</span>
            </span>
          )}
        </CardContent>
      </Card>

      {isPending ? (
        <Loading />
      ) : error ? (
        <ErrorState error={error} />
      ) : data.companies.length === 0 ? (
        <EmptyState title="No company matches these filters" icon={Search}>
          Widen the search, or clear a filter.
        </EmptyState>
      ) : (
        <Card className="panel enter mt-4 overflow-hidden rounded-xl py-0">
          <Table>
            <TableHeader className="bg-muted/40">
              <TableRow>
                <TableHead>Company</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Turn</TableHead>
                <TableHead className="text-right">Apps</TableHead>
                <TableHead className="text-right">Messages</TableHead>
                <TableHead className="text-right">Last touch</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.companies.map((row) => (
                <TableRow key={row.id} className="group">
                  {/* Domain rides under the name rather than in a column
                      of its own: it is how you recognise the company, not a
                      value anyone compares down the page, and the column it
                      used to occupy pushed Last touch off the card. */}
                  <TableCell className="font-medium">
                    <span className="flex items-center gap-2.5">
                      <CompanyMark id={row.id} name={row.canonical_name} size="md" />
                      <span className="grid min-w-0">
                        <span className="flex min-w-0 items-center gap-2">
                          <Link
                            to={`/company/${row.id}`}
                            className="group-hover:text-primary truncate hover:underline"
                          >
                            {row.canonical_name}
                          </Link>
                          {row.kind === "agency" && <KindBadge kind="agency" />}
                        </span>
                        <span className="text-muted-foreground truncate font-mono text-[11px] font-normal">
                          {row.domain || "no domain"}
                        </span>
                      </span>
                    </span>
                  </TableCell>
                  <TableCell>
                    <StageBadge stage={row.latest_stage} />
                  </TableCell>
                  <TableCell>
                    <TurnBadge turn={row.turn} />
                  </TableCell>
                  <TableCell className="tabular text-right whitespace-nowrap">
                    {row.application_count}
                  </TableCell>
                  <TableCell className="tabular text-right">{row.message_count}</TableCell>
                  <TableCell className="tabular text-muted-foreground text-right whitespace-nowrap">
                    {fmtIso(row.last_touch)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  )
}
