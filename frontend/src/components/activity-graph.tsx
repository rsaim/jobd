/**
 * Interview days, in the contribution-graph shape.
 *
 * Two selectable series (`calendar.metric`, chosen by the hosting page's
 * picker). The default, "interviews", is confirmed interview rounds — a
 * recruiter screen only counts when outbound mail near its date says a
 * conversation actually happened, so a day full of unanswered recruiter
 * pitches reads as silent. "messages" is the traffic view: inbound
 * job-linked mail per day.
 *
 * The grid arrives pre-laid-out from the server, including each cell's ramp
 * level. The client renders an encoding it is handed; it does not decide one.
 * Every cell is a link that filters /messages to that day, and carries its
 * count in a title and aria-label because the ramp is never the only channel.
 */

import { Link } from "react-router-dom"

import type { ActivityCalendar } from "@/lib/api"
import { cn } from "@/lib/utils"

const RAMP = [
  "var(--act-0)",
  "var(--act-1)",
  "var(--act-2)",
  "var(--act-3)",
  "var(--act-4)",
]

export function ActivityGraph({
  calendar,
  mini = false,
  scope,
}: {
  calendar: ActivityCalendar
  mini?: boolean
  /** Extra query, e.g. `company=<uuid>`, so a cell filters to that day *for
   *  this company* rather than across the whole mailbox. */
  scope?: string
}) {
  const weeks = calendar.weeks
  const noun = calendar.metric === "messages" ? "email" : "interview"
  // Cells cap at 15px so the graph keeps the contribution-grid shape at any
  // column count. Without the cap a short grid (a young record, a narrow
  // range) let flex-1 columns fill the panel and the aspect-square cells
  // drove the row height up with them — a bar chart wearing a heatmap's
  // clothes. The wrapper's max-width is what enforces it: 15px cells + the
  // 3px gap, so a full 53-week year still fills, and anything narrower stays
  // cell-sized instead of stretching.
  const capped = !mini ? { maxWidth: `${weeks.length * 18}px` } : undefined
  return (
    <div className="flex flex-col gap-2">
      <div className={cn(mini && "overflow-x-auto")} style={capped}>
        {!mini && (
          <div className="relative mb-2 h-4">
            {calendar.month_labels.map(([index, label]) => (
              <span
                key={`${index}-${label}`}
                className="absolute font-mono text-[10px] uppercase tracking-wider text-muted-foreground"
                style={{ left: `${(index / weeks.length) * 100}%` }}
              >
                {label}
              </span>
            ))}
          </div>
        )}
        <div className={cn("flex gap-[3px]", mini ? "w-max" : "w-full")}>
          {weeks.map((week, w) => (
            <div
              key={w}
              className={cn("flex flex-col gap-[3px]", mini ? "w-2" : "min-w-0 flex-1")}
            >
              {week.map((cell, d) =>
                cell === null ? (
                  <span
                    key={d}
                    className={cn("rounded-[2px]", mini ? "size-2" : "aspect-square w-full")}
                  />
                ) : (
                  <Link
                    key={d}
                    to={`/messages?on=${cell.day}${scope ? `&${scope}` : ""}`}
                    title={`${cell.count} ${noun}${cell.count === 1 ? "" : "s"} on ${cell.day}`}
                    aria-label={`${cell.count} ${noun}${
                      cell.count === 1 ? "" : "s"
                    } on ${cell.day}`}
                    className={cn(
                      "hover:ring-primary rounded-[3px] transition-transform hover:scale-125 hover:ring-2",
                      mini ? "size-2" : "aspect-square w-full",
                    )}
                    style={{ background: RAMP[cell.level] }}
                  />
                ),
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="text-muted-foreground flex flex-wrap items-center gap-2 font-mono text-[11px]">
        <span>
          <b className="text-foreground font-semibold">
            {calendar.total.toLocaleString()}
          </b>{" "}
          {noun}s ·{" "}
          <b className="text-foreground font-semibold">{calendar.active_days}</b> active
          days
          {calendar.busiest && !mini
            ? ` · busiest ${calendar.busiest.count} on ${calendar.busiest.day}`
            : ""}
        </span>
        {!mini && (
          <span className="ml-auto flex items-center gap-1">
            Less
            {RAMP.map((color) => (
              <span
                key={color}
                className="size-2.5 rounded-[3px]"
                style={{ background: color }}
              />
            ))}
            More
          </span>
        )}
      </div>
    </div>
  )
}
