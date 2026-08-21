/**
 * One company: a generated summary and its whole mail chain.
 *
 * Identity, Activity, People, Audit, and "not job-related" moved to the
 * left nav rail (components/company-rail.tsx, shown by shell.tsx while on
 * this route). The applications/stage-timeline table that used to sit here
 * is gone on purpose, not merely unwired — a communications touch point
 * isn't the same thing as an application, and the per-application stage
 * detail it showed is now folded into the summary's own LLM call
 * (components/company-summary.tsx) instead of a second, separately
 * maintained rendering of `timeline.applications`. This page is the summary
 * panel and the communications chain, full width, nothing beside them.
 */

import { Link, useParams, useSearchParams } from "react-router-dom"
import { Building2, ChevronLeft, Mail, Search } from "lucide-react"

import { useCompany, qs } from "@/lib/api"
import { fmtMonth } from "@/lib/record"
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
import { CompanySummary } from "@/components/company-summary"
import { ReplyBox } from "@/components/reply-box"
import { MessageRow } from "@/components/message-row"
import { CompanyMark } from "@/components/company-mark"
import { KindBadge, TurnBadge } from "@/components/record-marks"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
} from "@/components/page"

const ANY = "any"

export function CompanyPage() {
  const { companyId = "" } = useParams()
  const [params, setParams] = useSearchParams()
  const direction = params.get("direction") ?? ""
  const channel = params.get("channel") ?? ""
  const evidencing = params.get("stage_evidencing") ?? ""
  const order = params.get("order") ?? "desc"
  const search = params.get("search") ?? ""

  const query = qs({ direction, channel, stage_evidencing: evidencing, order, search })
  const { data, isPending, error } = useCompany(companyId, query)

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value && value !== ANY) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { timeline, communications, turn } = data

  return (
    <div className="space-y-2">
      <Button asChild variant="ghost" size="sm" className="-ml-2 mb-1 text-muted-foreground">
        <Link to="/companies">
          <ChevronLeft /> Companies
        </Link>
      </Button>

      <PageHead
        icon={Building2}
        plate={
          <CompanyMark
            id={timeline.company_id}
            name={timeline.canonical_name}
            size="lg"
          />
        }
        eyebrow={`${timeline.kind} record`}
        title={timeline.canonical_name}
        lede={
          <>
            {fmtMonth(timeline.first_seen_at)} – {fmtMonth(timeline.last_seen_at)} ·{" "}
            {communications.length} message{communications.length === 1 ? "" : "s"}
          </>
        }
        actions={
          <>
            <TurnBadge turn={turn} live />
            <KindBadge kind={timeline.kind} />
          </>
        }
      />

      <div className="mt-6">
        <CompanySummary companyId={timeline.company_id} autoStart />
      </div>

      <div className="mt-3">
        <ReplyBox communications={communications} />
      </div>

      <SectionHead icon={Mail} count={communications.length}>
        Communications
      </SectionHead>
      <Card className="panel mb-4 rounded-xl py-3">
        <CardContent className="flex flex-wrap items-center gap-2 px-3">
          <Select value={direction || ANY} onValueChange={(v) => set("direction", v)}>
            <SelectTrigger size="sm" className="w-40" aria-label="Direction">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any direction</SelectItem>
              <SelectItem value="inbound">Inbound</SelectItem>
              <SelectItem value="outbound">Outbound</SelectItem>
            </SelectContent>
          </Select>
          <Select value={channel || ANY} onValueChange={(v) => set("channel", v)}>
            <SelectTrigger size="sm" className="w-36" aria-label="Channel">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any channel</SelectItem>
              <SelectItem value="email">Email</SelectItem>
              <SelectItem value="linkedin">LinkedIn</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={evidencing || ANY}
            onValueChange={(v) => set("stage_evidencing", v)}
          >
            <SelectTrigger size="sm" className="w-44" aria-label="Evidence filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All messages</SelectItem>
              <SelectItem value="true">Evidence only</SelectItem>
              <SelectItem value="false">Non-evidencing</SelectItem>
            </SelectContent>
          </Select>
          <Select value={order} onValueChange={(v) => set("order", v)}>
            <SelectTrigger size="sm" className="w-40" aria-label="Order">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">Newest first</SelectItem>
              <SelectItem value="asc">Oldest first</SelectItem>
            </SelectContent>
          </Select>
          <InputGroup className="h-8 w-64">
            <InputGroupAddon>
              <Search className="size-3.5" />
            </InputGroupAddon>
            <InputGroupInput
              defaultValue={search}
              placeholder="Search this company's mail"
              className="text-xs"
              onKeyDown={(e) => e.key === "Enter" && set("search", e.currentTarget.value)}
              onBlur={(e) => set("search", e.currentTarget.value)}
            />
          </InputGroup>
        </CardContent>
      </Card>

      {communications.length ? (
        <Card className="panel enter overflow-hidden rounded-xl py-0">
          {communications.map((message) => (
            <MessageRow key={message.id} message={message} defaultOpen={false} />
          ))}
        </Card>
      ) : (
        <EmptyState title="No messages match these filters" icon={Search} />
      )}
    </div>
  )
}
