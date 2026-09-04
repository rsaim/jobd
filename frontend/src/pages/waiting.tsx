/**
 * Companies whose last message came in — your reply is the thing missing.
 *
 * Newest first, because a reply owed since Tuesday is a different kind of
 * debt from one owed since last year: the top of this list is what you can
 * still answer without an apology.
 *
 * Home shows the same signal bounded to a briefing (25 rows, 120 days) so it
 * can answer "what should I do today". This is the whole backlog, which is
 * the other question — who am I still on the hook for — and it needs every
 * row to answer it honestly.
 */

import { useRange } from "@/lib/range-context"
import { Reply } from "lucide-react"

import { useWaiting } from "@/lib/api"
import { BriefingTable } from "@/components/briefing-table"
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/page"

export function WaitingPage() {
  const { query } = useRange()
  const { data, isPending, error } = useWaiting(query)
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { waiting } = data
  const recent = waiting.filter((row) => row.days_silent <= 30).length

  return (
    <div className="space-y-2">
      <PageHead
        icon={Reply}
        eyebrow="The search, today"
        title="Review"
        lede={
          waiting.length
            ? `${waiting.length} ${waiting.length === 1 ? "company is" : "companies are"} waiting on you, ${recent} within the last 30 days. Newest first.`
            : "Nobody is waiting on a reply from you."
        }
      />

      {waiting.length === 0 ? (
        <EmptyState title="Nothing owed" icon={Reply}>
          Every company you have mail from has heard back from you, or has
          already finished its process.
        </EmptyState>
      ) : (
        /* The table, not the card grid: this is read by scanning down the
           silence column to find the oldest debt, and a column is what a
           card grid refuses to give you (briefing-table.tsx's docstring). */
        <BriefingTable rows={waiting} />
      )}
    </div>
  )
}
