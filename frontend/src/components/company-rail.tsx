/**
 * Identity, Activity, People, Audit, and the "not job-related" control —
 * moved out of the company page's own content into the left nav rail,
 * per request. Shown only on `/company/<id>` (shell.tsx checks the route),
 * only while the rail is expanded (icon-collapsed mode has no room for a
 * form), below the rail's own nav links so Home/Companies/etc. never move.
 *
 * A second, independent fetch of the same company (useCompany with no
 * filters) rather than lifting company.tsx's own query up into the shell —
 * that would mean the shell knowing about a specific page's data shape,
 * which is exactly the coupling the rest of this app avoids between pages.
 * TanStack Query still dedupes anything genuinely concurrent; this is one
 * extra request, not a heavier one.
 */

import { useState } from "react"
import { Link } from "react-router-dom"
import {
  BadgeCheck,
  CalendarRange,
  Check,
  IdCard,
  Trash2,
  Users,
} from "lucide-react"
import { toast } from "sonner"

import { useActivity, useCompany, useWrite } from "@/lib/api"
import type { ActivityCalendar, ActivityMetric, PersonRow } from "@/lib/api"
import { words } from "@/lib/record"
import { ActivityGraph } from "@/components/activity-graph"
import { StageBadge, Tag } from "@/components/record-marks"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"

export function CompanyRailPanels({ companyId }: { companyId: string }) {
  const { data, isPending, error } = useCompany(companyId, "")
  if (isPending || error || !data) return null
  const { timeline, people, calendar, verification } = data

  return (
    <>
      <IdentityRailPanel
        companyId={timeline.company_id}
        name={timeline.canonical_name}
        domain={timeline.domain}
        kind={timeline.kind}
      />
      <ActivityRailPanel companyId={timeline.company_id} initial={calendar} />
      <PeopleRailPanel people={people} companyId={timeline.company_id} />
      {verification && (
        <AuditRailPanel verification={verification} companyName={timeline.canonical_name} />
      )}
      <NotJobRelatedRailPanel companyId={timeline.company_id} companyName={timeline.canonical_name} />
    </>
  )
}

/** The mini activity grid with the same metric picker the home page has —
 *  the payload ships the interviews series; email traffic is fetched from
 *  /api/activity scoped to this company on demand. */
function ActivityRailPanel({
  companyId,
  initial,
}: {
  companyId: string
  initial: ActivityCalendar
}) {
  const [metric, setMetric] = useState<ActivityMetric>("interviews")
  const fetched = useActivity(metric, companyId)
  const calendar = metric === "interviews" ? initial : fetched.data
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase">
        <CalendarRange className="size-3" /> Activity
        <Select
          value={metric}
          onValueChange={(value) => setMetric(value as ActivityMetric)}
        >
          <SelectTrigger
            size="sm"
            className="ml-auto h-6 w-auto border-none px-1.5 font-mono text-[10px] shadow-none"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="interviews">Interviews</SelectItem>
            <SelectItem value="messages">Emails</SelectItem>
          </SelectContent>
        </Select>
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2">
        {calendar ? (
          <ActivityGraph calendar={calendar} mini scope={`company=${companyId}`} />
        ) : (
          <p className="text-muted-foreground px-1 text-[11px]">Loading…</p>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

function IdentityRailPanel({
  companyId,
  name,
  domain,
  kind,
}: {
  companyId: string
  name: string
  domain: string | null
  kind: string
}) {
  const [form, setForm] = useState({ canonical_name: name, domain: domain ?? "", kind })
  const rename = useWrite<typeof form & { id: string }>(
    (v) => `/company/${v.id}/rename`,
    ["company", "companies", "home"],
    (v) => ({ canonical_name: v.canonical_name, domain: v.domain, kind: v.kind }),
  )
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase">
        <IdCard className="size-3" /> Identity
      </SidebarGroupLabel>
      <SidebarGroupContent className="grid gap-1.5 px-2">
        <Input
          value={form.canonical_name}
          onChange={(e) => setForm({ ...form, canonical_name: e.target.value })}
          className="h-7 text-xs"
          aria-label="Canonical name"
        />
        <Input
          value={form.domain}
          onChange={(e) => setForm({ ...form, domain: e.target.value })}
          placeholder="Web domain"
          className="h-7 text-xs"
          aria-label="Domain"
        />
        <Select value={form.kind} onValueChange={(value) => setForm({ ...form, kind: value })}>
          <SelectTrigger size="sm" className="h-7 text-xs" aria-label="Kind">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="employer">Employer</SelectItem>
            <SelectItem value="agency">Agency</SelectItem>
          </SelectContent>
        </Select>
        <Button
          size="sm"
          className="h-7 w-fit text-xs"
          disabled={rename.isPending}
          onClick={() =>
            rename.mutate(
              { ...form, id: companyId },
              { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
            )
          }
        >
          <Check /> Save
        </Button>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

function PeopleRailPanel({
  people,
  companyId,
}: {
  people: PersonRow[]
  companyId: string
}) {
  const humans = people.filter((p) => !p.is_system)
  const systems = people.filter((p) => p.is_system)
  const setRole = useWrite<{ contactId: string; company_id: string; role_title: string }>(
    (v) => `/contact/${v.contactId}/role`,
    ["company"],
    (v) => ({ company_id: v.company_id, role_title: v.role_title }),
  )
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase">
        <Users className="size-3" /> People <Tag className="ml-1">{humans.length}</Tag>
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2 text-xs">
        {humans.length === 0 && (
          <p className="text-muted-foreground text-[11.5px]">No people recorded.</p>
        )}
        {humans.map((person) => (
          <div key={person.contact_id} className="border-b py-2 last:border-b-0">
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-medium">{person.name}</span>
              <Tag>{person.message_count}</Tag>
            </div>
            <div className="text-muted-foreground truncate font-mono text-[10.5px]">
              {person.address}
            </div>
            {person.stages.length > 0 && (
              <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-1 text-[10.5px]">
                {person.stages.map((stage) => (
                  <StageBadge key={stage} stage={stage} />
                ))}
              </div>
            )}
            {person.company_count > 1 && (
              <Link
                to={`/contact/${person.contact_id}`}
                className="mt-1 inline-block text-[10.5px] underline underline-offset-4"
              >
                +{person.company_count - 1} more
              </Link>
            )}
            <Input
              defaultValue={person.role_title ?? ""}
              placeholder="Role, e.g. recruiter"
              aria-label="Role at this company"
              className="mt-1.5 h-6 text-[11px]"
              onBlur={(event) =>
                event.currentTarget.value !== (person.role_title ?? "") &&
                setRole.mutate(
                  {
                    contactId: person.contact_id,
                    company_id: companyId,
                    role_title: event.currentTarget.value,
                  },
                  { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                )
              }
            />
          </div>
        ))}
        {systems.length > 0 && (
          <Collapsible className="mt-1">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="text-muted-foreground h-6 px-0 text-[10.5px]">
                {systems.length} automated
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              {systems.map((person) => (
                <div key={person.contact_id} className="border-b py-1.5 last:border-b-0">
                  <span className="truncate font-medium">{person.name}</span>
                  <div className="text-muted-foreground truncate font-mono text-[10.5px]">
                    {person.address}
                  </div>
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

function AuditRailPanel({
  verification,
  companyName,
}: {
  verification: NonNullable<ReturnType<typeof useCompany>["data"]>["verification"]
  companyName: string
}) {
  const apply = useWrite<{
    match_type: string
    value: string
    verdict: string
    company_name: string
  }>(() => "/verification/apply", ["company", "triage", "companies"])
  if (!verification) return null
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="flex items-center justify-between gap-2 font-mono text-[10px] tracking-[0.16em] uppercase">
        <span className="flex items-center gap-1.5">
          <BadgeCheck className="size-3" /> Audit
        </span>
        <Badge variant={verification.classification_correct ? "secondary" : "destructive"}>
          {verification.classification_correct ? "confirmed" : "flagged"}
        </Badge>
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2 text-[11px]">
        {verification.reasoning && (
          <p className="text-muted-foreground mb-1.5">{words(verification.reasoning).slice(0, 160)}</p>
        )}
        {verification.suggested_rules.map((rule, index) => (
          <div key={index} className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <Badge variant={rule.verdict === "negative" ? "destructive" : "secondary"}>
              {rule.verdict}
            </Badge>
            <code className="bg-muted truncate rounded px-1 py-0.5 font-mono text-[10px]">
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
                    company_name: companyName,
                  },
                  { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                )
              }
            >
              Apply
            </Button>
          </div>
        ))}
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

function NotJobRelatedRailPanel({
  companyId,
  companyName,
}: {
  companyId: string
  companyName: string
}) {
  const remove = useWrite<{ id: string }>(
    (v) => `/company/${v.id}/negative`,
    ["company", "companies", "home", "messages"],
    () => ({}),
  )
  return (
    <SidebarGroup>
      <SidebarGroupLabel className="gap-1.5 font-mono text-[10px] tracking-[0.16em] uppercase">
        Not job-related?
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2">
        <Button
          variant="destructive"
          size="sm"
          className="h-7 w-full text-xs"
          disabled={remove.isPending}
          onClick={() => {
            if (!window.confirm(`Remove ${companyName} and mark its mail not job-related?`)) return
            remove.mutate(
              { id: companyId },
              { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
            )
          }}
        >
          <Trash2 /> Remove this company
        </Button>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
