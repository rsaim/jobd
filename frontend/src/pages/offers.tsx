/**
 * Every offer ever received, one list, newest first.
 *
 * It used to split into "Awaiting your decision" and "Resolved". The split
 * asked the reader to hold two lists to answer one question -- what offers
 * have I had -- and the same company could sit in either depending on a
 * stage transition it had no control over. The stage is on each row, which
 * is where a per-row fact belongs; a heading is the wrong instrument for it.
 *
 * Its own page, not a Home section: an offer is a decision, and In flight
 * already shows it among five other mid-process stages
 * (services/dashboard.py's offers() docstring).
 */

import { useRange } from "@/lib/range-context"
import { Award } from "lucide-react"

import { useOffers } from "@/lib/api"
import { BriefingGrid } from "@/components/briefing-grid"
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/page"

export function OffersPage() {
  const { query } = useRange()
  const { data, isPending, error } = useOffers(query)
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { offers } = data
  const companies = new Set(offers.map((row) => row.canonical_name)).size

  return (
    <div className="space-y-2">
      <PageHead
        icon={Award}
        eyebrow="The search, today"
        title="Offers"
        lede={
          offers.length
            ? `${companies} ${companies === 1 ? "company" : "companies"} extended an offer, across ${offers.length} ${offers.length === 1 ? "application" : "applications"}.`
            : "Nothing in the record has reached the offer stage."
        }
      />

      {offers.length === 0 ? (
        <EmptyState title="No offers yet" icon={Award}>
          Nothing in the record has reached the offer stage.
        </EmptyState>
      ) : (
        /* `showStage` on: with one list the stage is what tells a live offer
           from a resolved one, and it is the reading the two headings used
           to carry. */
        <BriefingGrid rows={offers} showStage />
      )}
    </div>
  )
}
