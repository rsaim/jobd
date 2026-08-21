/**
 * Page furniture: the title block, the readout band, section headings, empty
 * states and the loading skeletons.
 *
 * A dashboard is opened twenty times a day, so density is the point: the
 * spacing here is deliberately tighter than a marketing page's would be, and
 * the numbers are tabular so a column of them can be compared by eye.
 *
 * The visual language is an instrument panel. Headings are set in the display
 * face and carry an icon in a tinted plate so a page is recognisable before
 * it is read; a section rule is a measuring rule with a count on it; and a
 * statistic is a readout — micro label above, mono value below, a hairline
 * under it — rather than a card with a big number in it.
 */

import type { ComponentType, ReactNode } from "react"
import { CircleAlert, Inbox } from "lucide-react"

import { cn } from "@/lib/utils"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

type Icon = ComponentType<{ className?: string }>

export function PageHead({
  eyebrow,
  title,
  lede,
  actions,
  icon: IconMark,
  plate,
}: {
  eyebrow?: string
  title: ReactNode
  lede?: ReactNode
  actions?: ReactNode
  icon?: Icon
  /** A mark of its own in place of the icon plate — a company page opens on
   *  that company's logo, which identifies the page faster than any glyph
   *  this app could pick for "a company". Falls back to `icon` when there is
   *  nothing to show. */
  plate?: ReactNode
}) {
  return (
    <div className="enter flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 gap-3.5">
        {plate ? (
          <span className="mt-0.5 shrink-0">{plate}</span>
        ) : (
          IconMark && (
          <span
            aria-hidden
            className="from-primary/15 to-primary/5 text-primary ring-primary/15 mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl bg-gradient-to-b ring-1"
          >
            <IconMark className="size-[18px]" />
          </span>
          )
        )}
        <div className="min-w-0">
          {eyebrow && (
            <p className="text-muted-foreground mb-1 font-mono text-[10.5px] tracking-[0.18em] uppercase">
              {eyebrow}
            </p>
          )}
          <h1 className="display text-[26px] leading-tight font-semibold text-balance">
            {title}
          </h1>
          {lede && (
            <p className="text-muted-foreground mt-1.5 max-w-2xl text-sm">{lede}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function SectionHead({
  children,
  count,
  icon: IconMark,
  actions,
  className,
}: {
  children: ReactNode
  count?: number
  icon?: Icon
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("mt-9 mb-3 flex items-center gap-2.5", className)}>
      {IconMark && (
        <span
          aria-hidden
          className="bg-muted text-muted-foreground ring-border grid size-6 shrink-0 place-items-center rounded-md ring-1"
        >
          <IconMark className="size-3.5" />
        </span>
      )}
      <h2 className="font-mono text-[11px] tracking-[0.14em] uppercase">{children}</h2>
      {count !== undefined && (
        <span className="tabular bg-muted text-muted-foreground rounded-full px-1.5 py-0.5 text-[10.5px] leading-none">
          {count}
        </span>
      )}
      {/* The rule is a measuring rule: it starts at full strength beside the
          label and fades out, so the eye reads left-to-right and the line
          never becomes a box. */}
      <span className="from-border h-px flex-1 bg-gradient-to-r to-transparent" />
      {actions}
    </div>
  )
}

/** One reading on the panel. `tone` tints the plate when the number is the
 *  headline one; `accent` paints the value itself with a reserved status
 *  colour, and is only ever used where a word says the same thing. */
export function Stat({
  value,
  label,
  hint,
  sub,
  lead = false,
  icon: IconMark,
  accent,
  small = false,
}: {
  value: ReactNode
  label: string
  hint?: string
  /** A second, smaller reading on the same plate — the qualifier that would
   *  otherwise need a tile of its own ("~140h of it"). */
  sub?: ReactNode
  lead?: boolean
  icon?: Icon
  accent?: string
  /** For readings that are words or dates rather than magnitudes — a date at
   *  display size wraps onto two lines and stops being a reading. */
  small?: boolean
}) {
  return (
    <Card
      className={cn(
        "panel relative gap-0 overflow-hidden rounded-xl px-4 py-3.5",
        lead && "from-primary/[0.07] border-primary/20 bg-gradient-to-br to-transparent",
      )}
      title={hint}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-muted-foreground font-mono text-[10px] tracking-[0.14em] uppercase">
          {label}
        </p>
        {IconMark && (
          <IconMark
            className={cn("size-3.5 shrink-0", lead ? "text-primary" : "text-muted-foreground/60")}
          />
        )}
      </div>
      <div
        className={cn(
          "tabular mt-2.5 leading-none font-semibold tracking-tight",
          small ? "text-[17px]" : "text-[26px]",
        )}
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </div>
      {/* The hairline under a readout is the panel's tick mark — it gives the
          number a baseline to sit on, which is what separates a reading from
          a floating figure. */}
      <div className="bg-border/70 mt-3 h-px w-full" />
      {sub && (
        <p className="text-muted-foreground mt-1.5 font-mono text-[10.5px]">{sub}</p>
      )}
    </Card>
  )
}

export function StatBand({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "enter mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6",
        className,
      )}
    >
      {children}
    </div>
  )
}

export function EmptyState({
  title,
  children,
  icon: IconMark = Inbox,
}: {
  title: string
  children?: ReactNode
  icon?: Icon
}) {
  return (
    <Empty className="bg-muted/30 rounded-xl border border-dashed py-10">
      <EmptyHeader>
        <EmptyMedia variant="icon" className="bg-background ring-border ring-1">
          <IconMark className="size-5" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        {children && <EmptyDescription>{children}</EmptyDescription>}
      </EmptyHeader>
    </Empty>
  )
}

export function Loading({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-6">
      <div className="flex gap-3.5">
        <Skeleton className="size-10 rounded-xl" />
        <div className="space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-6 w-56" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-[92px] rounded-xl" />
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} className="h-28 rounded-xl" />
        ))}
      </div>
    </div>
  )
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong."
  return (
    <Empty className="border-destructive/30 bg-destructive/5 rounded-xl border py-10">
      <EmptyHeader>
        <EmptyMedia
          variant="icon"
          className="bg-destructive/10 text-destructive ring-destructive/20 ring-1"
        >
          <CircleAlert className="size-5" />
        </EmptyMedia>
        <EmptyTitle>That didn't load</EmptyTitle>
        <EmptyDescription className="font-mono text-xs">{message}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
