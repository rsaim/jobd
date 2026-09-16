/**
 * The card grid shared by Home's lists and the Offers page — one company
 * per card, the silence rail and current stage underneath. Split out of
 * home.tsx once a second page (offers.tsx) needed the exact same rendering
 * for the exact same row shape (`BriefingRow`).
 */

import { Link } from "react-router-dom"
import { ArrowUpRight } from "lucide-react"

import type { BriefingRow } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { CompanyMark } from "@/components/company-mark"
import { Marked } from "@/components/highlight"
import { KindBadge, SilenceRail, StageBadge } from "@/components/record-marks"

function BriefCard({
  row,
  prefix,
  mark,
  showStage = true,
}: {
  row: BriefingRow
  prefix?: string
  /** Off on pages whose sections already name the status (Offers groups
   *  rows under "Awaiting your decision" / "Resolved" — a stage badge on
   *  every card there repeats the header). */
  showStage?: boolean
  /** Filter words to mark in the two fields a filter usually matched on.
   *  Without this a filtered grid is a set of cards with no visible reason
   *  for being the survivors. */
  mark?: string[]
}) {
  return (
    // `min-w-0` is load-bearing: a grid item's default min-width is
    // auto, so on one column the longest subject line sets the card's
    // width and the page scrolls sideways on a phone.
    <Card className="panel panel-lift hover:border-primary/30 group relative min-w-0 gap-0 rounded-xl py-3.5">
      <CardContent className="flex h-full flex-col gap-2.5 px-3.5">
        <div className="flex min-w-0 items-center gap-2">
          <CompanyMark id={row.company_id} name={row.canonical_name} size="md" />
          <Link
            to={`/company/${row.company_id}`}
            className="truncate font-medium after:absolute after:inset-0 hover:underline max-sm:whitespace-normal max-sm:[overflow-wrap:anywhere]"
          >
            <Marked text={row.canonical_name} words={mark ?? []} />
          </Link>
          {row.kind === "agency" && <KindBadge kind="agency" />}
          <ArrowUpRight className="text-muted-foreground ml-auto size-3.5 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
        </div>

        <p className="text-muted-foreground truncate text-[12.5px]">
          {prefix}
          <Marked text={row.last_subject || "(no subject)"} words={mark ?? []} />
        </p>
        {/* The role only earns a line when it is why this card is here —
            otherwise the card would carry a second title competing with the
            company name on every row. */}
        {row.role_title &&
          // `some`, not `every`: "acme senior" matches the card on the
          // company plus the role, and the role line is worth showing for
          // its half of that. A role the subject line already spells out is
          // not worth a second line, though — recruiters put the title in
          // the subject often enough that this fires on most cards.
          !(row.last_subject ?? "").toLowerCase().includes(row.role_title.toLowerCase()) &&
          mark?.some((word) => row.role_title!.toLowerCase().includes(word)) && (
            <p className="text-muted-foreground/80 truncate text-[11.5px]">
              <Marked text={row.role_title} words={mark} />
            </p>
          )}

        <div className="mt-auto space-y-2 pt-1">
          <SilenceRail days={row.days_silent} />
          {showStage && <StageBadge stage={row.status} />}
        </div>
      </CardContent>
    </Card>
  )
}

export function BriefingGrid({
  rows,
  prefix,
  mark,
  showStage,
}: {
  rows: BriefingRow[]
  prefix?: string
  mark?: string[]
  showStage?: boolean
}) {
  return (
    <div className="enter grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {rows.map((row) => (
        <BriefCard
          key={`${row.company_id}-${row.application_id}`}
          row={row}
          prefix={prefix}
          mark={mark}
          showStage={showStage}
        />
      ))}
    </div>
  )
}
