/**
 * Every application the company said no to. Its own page, mirroring Offers —
 * a rejection is an ending worth its own address, not a stage buried in a
 * table read for something else.
 */

import { useRange } from "@/lib/range-context"
import { XCircle } from "lucide-react"

import { useRejections } from "@/lib/api"
import { BriefingGrid } from "@/components/briefing-grid"
import { EmptyState, ErrorState, Loading, PageHead, SectionHead } from "@/components/page"

export function RejectionsPage() {
  const { query } = useRange()
  const { data, isPending, error } = useRejections(query)
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { rejections } = data

  return (
    <div className="space-y-2">
      <PageHead
        icon={XCircle}
        eyebrow="The search, today"
        title="Rejections"
        lede={
          rejections.length
            ? `${rejections.length} application${rejections.length === 1 ? "" : "s"} the company said no to.`
            : "No rejections on record."
        }
      />

      {rejections.length === 0 ? (
        <EmptyState title="No rejections yet" icon={XCircle}>
          Nothing in the record has been turned down.
        </EmptyState>
      ) : (
        <>
          <SectionHead icon={XCircle} count={rejections.length}>
            Rejected
          </SectionHead>
          <BriefingGrid rows={rejections} />
        </>
      )}
    </div>
  )
}
