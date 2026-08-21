/**
 * The record's vocabulary, in one place.
 *
 * Stage bands, outcome meanings and the date formats. These are not styling
 * choices, they are what the record means, and every page has to agree about
 * them: `declined` is the candidate saying no to an offer and is not a
 * rejection, `ghosted` is derived and was never recorded, and the seven
 * stages collapse into four readable bands because seven steps of one hue put
 * adjacent stages under the contrast floor.
 */

export const MAIN_STAGES = [
  "applied",
  "recruiter_screen",
  "phone_screen",
  "technical",
  "onsite",
  "offer",
] as const

export const TERMINAL_OUTCOMES = [
  "accepted",
  "declined",
  "rejected",
  "withdrawn",
] as const

/** Which of the four ramp bands a stage sits in. The exact stage is always
 *  written beside the mark, so the colour carries phase and not identity. */
export function stageBand(stage: string | null | undefined) {
  switch (stage) {
    case "applied":
      return "applied"
    case "recruiter_screen":
    case "phone_screen":
      return "screening"
    case "technical":
    case "onsite":
      return "deep"
    case "offer":
    case "accepted":
      return "outcome"
    case "declined":
      return "declined"
    case "rejected":
    case "withdrawn":
      return "critical"
    default:
      return "none"
  }
}

/** The band as a concrete colour. Inline style rather than a Tailwind class:
 *  these are data tokens, and letting them into the utility vocabulary is how
 *  a stage colour ends up on a button. */
export function bandColor(band: ReturnType<typeof stageBand>): string {
  switch (band) {
    case "applied":
      return "var(--stage-applied)"
    case "screening":
      return "var(--stage-screening)"
    case "deep":
      return "var(--stage-deep)"
    case "outcome":
      return "var(--stage-outcome)"
    case "declined":
      return "var(--stage-declined)"
    case "critical":
      return "var(--status-critical)"
    default:
      return "var(--muted-foreground)"
  }
}

export const words = (value: string | null | undefined) =>
  (value ?? "").replaceAll("_", " ")

export function turnLabel(turn: string) {
  if (turn === "your_turn") return "your turn"
  if (turn === "their_turn") return "their turn"
  return "no turn"
}

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
})
const MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric" })

/** Parse, or nothing. A row in this record can legitimately have no date —
 *  an ingest run that never reached a day, an application with no first
 *  message — and `new Date(null).toISOString()` throws a RangeError that
 *  takes the whole page down with it. A missing date is an em-dash, not an
 *  error boundary. */
const parse = (iso: string | null | undefined) => {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

export const fmtDay = (iso: string | null | undefined) => {
  const date = parse(iso)
  return date ? DAY.format(date) : "—"
}

export const fmtMonth = (iso: string | null | undefined) => {
  const date = parse(iso)
  return date ? MONTH.format(date) : "—"
}

/** ISO date, for columns that are compared down the page rather than read as
 *  prose. Sortable by eye, which a "16 Jun 2025" column is not. */
export const fmtIso = (iso: string | null | undefined) => {
  const date = parse(iso)
  return date ? date.toISOString().slice(0, 10) : "—"
}

export const fmtStamp = (iso: string | null | undefined) => {
  const date = parse(iso)
  return date ? date.toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—"
}

export const pct = (value: number) => `${Math.round(value * 100)}%`

/** Split a plain-text body into text and clickable-link segments.
 *
 * `body_text` is plain text — HTML is stripped at ingest (envelope.py) so
 * an attacker-controlled body can never reach the DOM as markup. This is
 * the safe half of "rich text": real anchors from bare URLs, built as React
 * nodes rather than `dangerouslySetInnerHTML`, so there is nothing here for
 * injected content to exploit — a matched substring can only ever become an
 * `<a href>`, never arbitrary markup. Also catches Outlook's
 * `Label<https://url>` bracket-link convention, which plain URL-matching
 * would otherwise render as two separate, oddly-adjacent links.
 */
const URL_RE = /(\w[\w.+-]*<)?(https?:\/\/[^\s<>()]+)(>)?/g

export function linkify(text: string): (string | { href: string; label: string })[] {
  const parts: (string | { href: string; label: string })[] = []
  let last = 0
  for (const match of text.matchAll(URL_RE)) {
    const [whole, labelPrefix, url] = match
    const start = match.index ?? 0
    if (start > last) parts.push(text.slice(last, start))
    const label = labelPrefix ? labelPrefix.slice(0, -1) : url
    parts.push({ href: url, label })
    last = start + whole.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}
