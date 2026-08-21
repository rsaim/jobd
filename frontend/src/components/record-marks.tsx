/**
 * The marks that carry record meaning: a stage, an outcome, whose turn it is,
 * and how long a thread has been silent.
 *
 * Chrome and data still never share a colour. What a mark may do is tint
 * itself from its OWN data token — a stage chip is washed with the stage
 * band's colour at 12%, ringed at 30%, and prints its label in the chrome
 * foreground so the text stays readable at any tint. That keeps the two
 * vocabularies separate (nothing here can ever come out looking like a
 * primary button or a focus ring) while letting the record be legible at a
 * glance instead of a wall of grey pills.
 *
 * Every mark ships with its word. Colour is never the only channel.
 */

import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"
import {
  Ban,
  BadgeCheck,
  CircleDot,
  Ghost,
  Handshake,
  Laptop,
  Network,
  Mail,
  MessagesSquare,
  PhoneCall,
  Send,
  ThumbsDown,
  Undo2,
  Users,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { bandColor, stageBand, turnLabel, words } from "@/lib/record"

/** The stage vocabulary, drawn.
 *
 *  Not decoration: seven stages collapse into four colour bands (the ramp
 *  cannot carry seven steps above the contrast floor), so colour says phase
 *  and the glyph says which stage inside it. `technical` and `onsite` share
 *  the deep band and would otherwise be the same chip with different words —
 *  a laptop and a room tell them apart before the word is read.
 *
 *  Every icon is still accompanied by its word. This adds a channel; it does
 *  not replace one. */
const STAGE_ICONS: Record<string, LucideIcon> = {
  applied: Send,
  recruiter_screen: MessagesSquare,
  phone_screen: PhoneCall,
  technical: Laptop,
  onsite: Users,
  offer: Handshake,
  accepted: BadgeCheck,
  declined: Undo2,
  rejected: ThumbsDown,
  withdrawn: Ban,
  ghosted: Ghost,
  open: CircleDot,
}

export function stageIcon(stage: string | null | undefined) {
  return stage ? STAGE_ICONS[stage] : undefined
}

/** Where a message came through. Two channels today, and the difference
 *  matters when reading a thread: a LinkedIn InMail and an email from the
 *  same recruiter are not the same conversation. Lucide dropped its brand
 *  glyphs, so LinkedIn is drawn as what it is here — a network — rather than
 *  with a logo this project would have to ship and maintain itself. */
export function channelIcon(channel: string | null | undefined) {
  return channel === "linkedin" ? Network : Mail
}

export function Dot({
  color,
  pulse = false,
  className,
}: {
  color: string
  pulse?: boolean
  className?: string
}) {
  return (
    <span
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        pulse && "motion-safe:animate-pulse",
        className,
      )}
      // No halo. A dot inside a DataChip already sits on a wash of its own
      // colour inside a border of its own colour; a third ring of the same
      // hue was the accessory to take off before leaving the house.
      style={{ background: color }}
    />
  )
}

/** A pill that takes its wash from a data token. */
export function DataChip({
  color,
  children,
  icon: Icon,
  dashed = false,
  pulse = false,
  className,
}: {
  color: string
  children: ReactNode
  /** A glyph in place of the dot. The dot says "this is a data mark"; an
   *  icon says that AND which mark, which is what a chip needs when four
   *  stages share one colour band. */
  icon?: LucideIcon
  dashed?: boolean
  pulse?: boolean
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex w-fit shrink-0 items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11.5px] leading-tight font-medium whitespace-nowrap",
        dashed && "border-dashed",
        className,
      )}
      style={{
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        borderColor: `color-mix(in oklab, ${color} 30%, transparent)`,
      }}
    >
      {Icon ? (
        <Icon className="size-3 shrink-0" style={{ color }} />
      ) : (
        <Dot color={color} pulse={pulse} />
      )}
      {children}
    </span>
  )
}

export function StageBadge({
  stage,
  className,
}: {
  stage: string | null | undefined
  className?: string
}) {
  if (!stage) return null
  return (
    <DataChip
      color={bandColor(stageBand(stage))}
      icon={stageIcon(stage)}
      className={className}
    >
      {words(stage)}
    </DataChip>
  )
}

/** An application's state: an explicit outcome, or `ghosted`, or `open`.
 *  `ghosted` is derived at read time and recorded nowhere, so it is drawn
 *  hollow — there is no message that says it. */
export function OutcomeBadge({ outcome }: { outcome: string }) {
  if (outcome === "ghosted") {
    return (
      <span className="text-muted-foreground inline-flex w-fit items-center gap-1.5 rounded-md border border-dashed px-1.5 py-0.5 text-[11.5px] leading-tight font-medium">
        <Ghost className="size-3 shrink-0" />
        ghosted
      </span>
    )
  }
  if (outcome === "open") {
    return (
      <DataChip color="var(--status-good)" icon={CircleDot}>
        open
      </DataChip>
    )
  }
  return <StageBadge stage={outcome} />
}

export function TurnBadge({ turn, live = false }: { turn: string; live?: boolean }) {
  const color =
    turn === "your_turn"
      ? "var(--status-good)"
      : turn === "their_turn"
        ? "var(--status-warning)"
        : "var(--muted-foreground)"
  return (
    <DataChip color={color} pulse={live && turn !== "none"}>
      {turnLabel(turn)}
    </DataChip>
  )
}

export function KindBadge({ kind }: { kind: string }) {
  return (
    <span
      className={cn(
        "inline-flex w-fit items-center rounded-md border px-1.5 py-0.5 text-[11px] leading-tight font-medium capitalize",
        kind === "agency"
          ? "text-muted-foreground border-dashed"
          : "bg-secondary text-secondary-foreground border-transparent",
      )}
    >
      {kind}
    </span>
  )
}

/* ------------------------------------------------------------------ *
 * Silence.
 *
 * The one quantity this record has that a CRM does not: how long a thread
 * has been quiet, measured against the thresholds the app itself acts on —
 * 14 days is "going cold", 21 is past it, 90 is dormant. It gets a gauge
 * rather than a number alone because the number is only meaningful relative
 * to those marks, and a reader should not have to remember them.
 *
 * The scale is logarithmic: the difference between day 2 and day 12 matters
 * and the difference between day 200 and day 210 does not, so a linear track
 * would spend all its length on the part nobody acts on.
 * ------------------------------------------------------------------ */

const DORMANT = 90
const at = (days: number) =>
  Math.min(1, Math.log1p(Math.max(0, days)) / Math.log1p(DORMANT))

/** A fresh thread is drawn in ink, not in green. Twenty cards of healthy
 *  green is a wash the eye stops reading, and it spends the alarm colours on
 *  the ordinary case: colour appears here only where something is going
 *  wrong, which is what makes the two amber bars in a grid findable. */
export function silenceTone(days: number): { color: string; word: string } {
  if (days >= DORMANT)
    return {
      color: "color-mix(in oklab, var(--muted-foreground) 45%, transparent)",
      word: "dormant",
    }
  if (days >= 21) return { color: "var(--status-critical)", word: "cold" }
  if (days >= 14) return { color: "var(--status-warning)", word: "cooling" }
  return {
    color: "color-mix(in oklab, var(--foreground) 55%, transparent)",
    word: "fresh",
  }
}

export function SilenceRail({
  days,
  className,
  showLabel = true,
}: {
  days: number
  className?: string
  showLabel?: boolean
}) {
  const { color, word } = silenceTone(days)
  const fill = at(days)
  return (
    <div
      className={cn("flex items-center gap-2", className)}
      title={`Silent ${days} day${days === 1 ? "" : "s"} — ${word}. Cold at 14, dormant at 90.`}
    >
      <div
        className="bg-muted relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full"
        role="meter"
        aria-valuenow={days}
        aria-valuemin={0}
        aria-valuemax={DORMANT}
        aria-label={`${days} days silent, ${word}`}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${fill * 100}%`, background: color }}
        />
        {/* The thresholds, drawn on the track. Without them the fill is a
            bar with no units; with them it says "you are past cold". */}
        {[14, 21].map((mark) => (
          <span
            key={mark}
            className="bg-background/70 absolute inset-y-0 w-px"
            style={{ left: `${at(mark) * 100}%` }}
          />
        ))}
      </div>
      {showLabel && (
        <span className="tabular text-muted-foreground shrink-0 text-[11px]">{days}d</span>
      )}
    </div>
  )
}

/** How a row got classified — free prefilter, free rule, or a paid model
 *  call. Typographic rather than another colour: the colour channel on these
 *  rows already means direction. */
export function Tag({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "text-muted-foreground font-mono text-[10px] tracking-wider uppercase",
        className,
      )}
    >
      {children}
    </span>
  )
}
