/**
 * The share card — a chrome-free rendering of the search's headline
 * numbers, sized to screenshot straight into a LinkedIn post. Lives
 * outside the Shell on purpose: no rail, no chat dock, no filter bar,
 * nothing that isn't worth showing a feed. The hero is the hours figure,
 * because "900 hours of interview work" is the sentence people stop on;
 * the funnel and the activity heatmap ground it in the record.
 */

import { Link } from "react-router-dom"
import { ArrowLeft, Flame } from "lucide-react"

import { useHome } from "@/lib/api"
import { pct } from "@/lib/record"
import { ActivityGraph } from "@/components/activity-graph"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ErrorState, Loading } from "@/components/page"

export function SharePage() {
  const { data, isPending, error } = useHome()
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const s = data.stats
  const rooms = Math.round(s.interview_hours)
  const prep = Math.round(s.prep_hours)
  const total = rooms + prep
  const funnel = [
    ["applied", s.total_applications],
    ["engaged", s.engaged],
    ["replied", s.replied],
    ["interviewed", s.interviewed],
    ["offers", s.offers],
  ] as const

  return (
    <div className="bg-background min-h-svh px-6 py-10">
      {/* The one control on the page, outside the card so a screenshot of
          the card never includes it. */}
      <div className="mx-auto mb-4 flex w-full max-w-[64rem] items-center justify-between">
        <Button asChild variant="ghost" size="sm">
          <Link to="/">
            <ArrowLeft /> Back to the record
          </Link>
        </Button>
        <p className="text-muted-foreground font-mono text-[11px]">
          screenshot the card below · it fits a feed
        </p>
      </div>

      <Card className="panel mx-auto w-full max-w-[64rem] gap-0 rounded-2xl px-10 py-9">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-muted-foreground font-mono text-[11px] tracking-[0.22em] uppercase">
            One job search, measured from the mailbox
          </p>
          <p className="text-muted-foreground font-mono text-[11px]">
            {new Date().toISOString().slice(0, 10)}
          </p>
        </div>

        <div className="mt-6 flex flex-wrap items-end gap-x-8 gap-y-4">
          <div>
            <div className="flex items-baseline gap-2">
              <span className="tabular text-[64px] leading-none font-semibold tracking-tight">
                ~{total}h
              </span>
              <Flame className="text-primary size-6" aria-hidden />
            </div>
            <p className="text-muted-foreground mt-2 text-[15px]">
              of interview work — <b className="text-foreground">{rooms}h</b> in
              interview rooms, <b className="text-foreground">{prep}h</b>{" "}
              preparing for them
            </p>
          </div>
          <div className="text-muted-foreground mb-1 grid gap-1 font-mono text-[12.5px]">
            <span>
              <b className="text-foreground">{s.interviews}</b> confirmed
              interview rounds
            </span>
            <span>
              <b className="text-foreground">{pct(s.response_rate)}</b> of
              companies I wrote to wrote back
            </span>
          </div>
        </div>

        <div className="mt-8">
          <ActivityGraph calendar={data.calendar} />
        </div>

        <div className="border-border/60 mt-7 flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t pt-5">
          {funnel.map(([label, n], i) => (
            <span key={label} className="flex items-center gap-2.5">
              {i > 0 && <span className="text-muted-foreground/50">→</span>}
              <span className="font-mono text-[13px]">
                <b className="tabular text-[15px]">{n}</b>{" "}
                <span className="text-muted-foreground">{label}</span>
              </span>
            </span>
          ))}
          <span className="text-muted-foreground/70 ml-auto font-mono text-[11px]">
            tracked by jobd, from the mail itself
          </span>
        </div>
      </Card>
    </div>
  )
}
