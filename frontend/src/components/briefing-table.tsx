/**
 * Home's lists as a table: one line per process, scanned down a column.
 *
 * Cards were the wrong instrument for this page. Fifty of them is a page of
 * boxes you read one at a time; the questions Home actually gets — who has
 * been quiet longest, which of these is at technical, what happened most
 * recently — are comparisons down a column, and a column is what a card grid
 * refuses to give you. The card layout survives on Offers, where six rows
 * with a number attached are a different job.
 *
 * Tight on purpose: 28px rows, one line each, no wrapping. The silence rail
 * keeps its own column because it is the measurement this record has and a
 * CRM does not, and side by side down a column it finally reads as a
 * comparison rather than as a decoration on a card.
 */

import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react"

import type { BriefingRow } from "@/lib/api"
import { fmtIso, MAIN_STAGES, TERMINAL_OUTCOMES } from "@/lib/record"
import { Card } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { CompanyMark } from "@/components/company-mark"
import { Marked } from "@/components/highlight"
import { KindBadge, SilenceRail, StageBadge } from "@/components/record-marks"

type SortKey = "company" | "stage" | "silence" | "touch"

/** Process order, so sorting by stage reads as progression rather than as
 *  the alphabet's opinion of it (onsite before applied). Outcomes rank past
 *  the live stages: a finished process sorts after one still moving. */
const STAGE_RANK: Record<string, number> = Object.fromEntries(
  [...MAIN_STAGES, ...TERMINAL_OUTCOMES].map((s, i) => [s, i]),
)

/** Descending feels primary for every column but the name: most recent
 *  touch, longest silence, furthest along. */
const DEFAULT_DIR: Record<SortKey, 1 | -1> = {
  company: 1,
  stage: -1,
  silence: -1,
  touch: -1,
}

function compare(a: BriefingRow, b: BriefingRow, key: SortKey): number {
  switch (key) {
    case "company":
      return a.canonical_name.localeCompare(b.canonical_name)
    case "stage":
      return (STAGE_RANK[a.status] ?? -1) - (STAGE_RANK[b.status] ?? -1)
    case "silence":
      return a.days_silent - b.days_silent
    case "touch":
      // ISO strings order lexically; a row with no date sorts to the bottom
      // in either direction rather than pretending to be the epoch.
      return (a.last_message_at ?? "").localeCompare(b.last_message_at ?? "")
  }
}

function SortHead({
  label,
  col,
  sort,
  onSort,
  className,
}: {
  label: string
  col: SortKey
  sort: { key: SortKey; dir: 1 | -1 }
  onSort: (col: SortKey) => void
  className?: string
}) {
  const active = sort.key === col
  const Arrow = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown
  return (
    <TableHead
      aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
      className={`text-muted-foreground h-8 py-0 font-mono text-[10px] tracking-[0.12em] uppercase ${className ?? ""}`}
    >
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`hover:text-foreground inline-flex items-center gap-1 uppercase ${active ? "text-foreground" : ""}`}
      >
        {label}
        <Arrow className={`size-3 ${active ? "" : "opacity-40"}`} />
      </button>
    </TableHead>
  )
}

export function BriefingTable({
  rows,
  prefix,
  mark,
}: {
  rows: BriefingRow[]
  prefix?: string
  /** Filter words to mark, so a filtered table says why each row survived. */
  mark?: string[]
}) {
  const words = mark ?? []
  // Last touch, newest first, is the default on every surface that renders
  // this table: "what just happened" is the question both Today's Review
  // panel and the Review page open with. Every measured column is a click
  // away from being the axis instead.
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({
    key: "touch",
    dir: -1,
  })
  const onSort = (col: SortKey) =>
    setSort((prev) =>
      prev.key === col
        ? { key: col, dir: prev.dir === 1 ? -1 : 1 }
        : { key: col, dir: DEFAULT_DIR[col] },
    )
  const sorted = useMemo(
    () => [...rows].sort((a, b) => compare(a, b, sort.key) * sort.dir),
    [rows, sort],
  )
  return (
    <Card className="panel enter overflow-hidden rounded-xl py-0">
      <Table className="text-[12.5px]">
        <TableHeader className="bg-muted/40">
          <TableRow className="hover:bg-transparent">
            <SortHead label="Company" col="company" sort={sort} onSort={onSort} />
            <TableHead className="text-muted-foreground h-8 py-0 font-mono text-[10px] tracking-[0.12em] uppercase">
              Last message
            </TableHead>
            <SortHead label="Stage" col="stage" sort={sort} onSort={onSort} className="w-40" />
            <SortHead label="Silence" col="silence" sort={sort} onSort={onSort} className="w-36" />
            <SortHead
              label="Last touch"
              col="touch"
              sort={sort}
              onSort={onSort}
              className="w-24 [&>button]:float-right"
            />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row) => (
            <TableRow
              key={`${row.company_id}-${row.application_id}`}
              className="group relative"
            >
              {/* `max-w-0` with `w-[N%]` is what makes truncation work in a
                  table: without it the longest subject sets the column width
                  and the page scrolls sideways instead of clipping. */}
              <TableCell className="w-[26%] max-w-0 py-1.5 font-medium">
                <span className="flex min-w-0 items-center gap-2">
                  <CompanyMark id={row.company_id} name={row.canonical_name} />
                  <Link
                    to={`/company/${row.company_id}`}
                    className="group-hover:text-primary truncate hover:underline after:absolute after:inset-0"
                  >
                    <Marked text={row.canonical_name} words={words} />
                  </Link>
                  {row.kind === "agency" && <KindBadge kind="agency" />}
                </span>
              </TableCell>

              <TableCell className="text-muted-foreground max-w-0 py-1.5">
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="truncate">
                    {prefix}
                    <Marked
                      text={row.last_subject || "(no subject)"}
                      words={words}
                    />
                  </span>
                  {/* The role earns space only when it is why this row is
                      here and the subject does not already say it. */}
                  {row.role_title &&
                    !(row.last_subject ?? "")
                      .toLowerCase()
                      .includes(row.role_title.toLowerCase()) &&
                    words.some((word) =>
                      row.role_title!.toLowerCase().includes(word),
                    ) && (
                      <span className="text-muted-foreground/70 shrink-0 truncate text-[11px]">
                        <Marked text={row.role_title} words={words} />
                      </span>
                    )}
                </span>
              </TableCell>

              <TableCell className="py-1.5">
                <StageBadge stage={row.status} />
              </TableCell>

              <TableCell className="py-1.5">
                <SilenceRail days={row.days_silent} />
              </TableCell>

              <TableCell className="tabular text-muted-foreground py-1.5 text-right text-[11.5px]">
                {fmtIso(row.last_message_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}
