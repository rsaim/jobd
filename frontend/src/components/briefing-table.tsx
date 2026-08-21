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

import { Link } from "react-router-dom"

import type { BriefingRow } from "@/lib/api"
import { fmtIso } from "@/lib/record"
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
  return (
    <Card className="panel enter overflow-hidden rounded-xl py-0">
      <Table className="text-[12.5px]">
        <TableHeader className="bg-muted/40">
          <TableRow className="hover:bg-transparent">
            <TableHead className="text-muted-foreground h-8 py-0 font-mono text-[10px] tracking-[0.12em] uppercase">
              Company
            </TableHead>
            <TableHead className="text-muted-foreground h-8 py-0 font-mono text-[10px] tracking-[0.12em] uppercase">
              Last message
            </TableHead>
            <TableHead className="text-muted-foreground h-8 w-40 py-0 font-mono text-[10px] tracking-[0.12em] uppercase">
              Stage
            </TableHead>
            <TableHead className="text-muted-foreground h-8 w-36 py-0 font-mono text-[10px] tracking-[0.12em] uppercase">
              Silence
            </TableHead>
            <TableHead className="text-muted-foreground h-8 w-24 py-0 text-right font-mono text-[10px] tracking-[0.12em] uppercase">
              Last touch
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
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
