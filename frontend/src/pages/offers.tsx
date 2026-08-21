/**
 * Every offer ever received — live ones still needing a decision, and
 * resolved ones kept for the record. Its own page, not a Home section: an
 * offer is a decision, and In flight already shows it among five other
 * mid-process stages (services/dashboard.py's offers() docstring).
 */

import { Award } from "lucide-react"

import { useOffers } from "@/lib/api"
import { BriefingGrid } from "@/components/briefing-grid"
import { EmptyState, ErrorState, Loading, PageHead, SectionHead } from "@/components/page"

export function OffersPage() {
  const { data, isPending, error } = useOffers()
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { offers } = data
  const open = offers.filter((row) => row.status === "offer")
  const resolved = offers.filter((row) => row.status !== "offer")

  return (
    <div className="space-y-2">
      <PageHead
        icon={Award}
        eyebrow="The search, today"
        title="Offers"
        lede={
          open.length
            ? `${open.length} open, waiting on a decision. ${resolved.length} already resolved.`
            : `Nothing open right now. ${resolved.length} resolved.`
        }
      />

      {offers.length === 0 ? (
        <EmptyState title="No offers yet" icon={Award}>
          Nothing in the record has reached the offer stage.
        </EmptyState>
      ) : (
        <>
          {open.length > 0 && (
            <>
              <SectionHead icon={Award} count={open.length}>
                Awaiting your decision
              </SectionHead>
              <BriefingGrid rows={open} showStage={false} />
            </>
          )}

          {resolved.length > 0 && (
            <>
              <SectionHead icon={Award} count={resolved.length}>
                Resolved
              </SectionHead>
              <BriefingGrid rows={resolved} showStage={false} />
            </>
          )}
        </>
      )}
    </div>
  )
}
