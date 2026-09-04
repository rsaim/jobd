/**
 * The global range control, in the header where it governs everything below.
 *
 * Grafana's placement, for Grafana's reason: a filter that scopes every panel
 * on the screen belongs above all of them, not inside one. Putting it on the
 * activity card would say it scopes the graph, which was the bug -- a 12-month
 * graph over a 20-month record, with no way to see the rest.
 */

import { Calendar, Check } from "lucide-react"

import { RANGES, rangeLabel } from "@/lib/range"
import { useRange } from "@/lib/range-context"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function RangePicker() {
  const { range, setRange } = useRange()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2 font-mono text-[11px]">
          <Calendar className="size-3.5" />
          {rangeLabel(range)}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        {RANGES.map((entry) => (
          <DropdownMenuItem
            key={entry.key}
            onSelect={() => setRange(entry.key)}
            className="justify-between text-[12.5px]"
          >
            {entry.label}
            {entry.key === range && <Check className="size-3.5" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
