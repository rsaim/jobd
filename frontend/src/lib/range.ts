/**
 * The global time range, Grafana-style.
 *
 * One range for the whole record: the funnel, the activity graph, offers,
 * rejections, review and messages all answer "in this window". Two panels on
 * one screen disagreeing about where the window starts is the failure this
 * exists to prevent, so the range is resolved to absolute instants ONCE here
 * and every request carries those same instants. A relative key sent to the
 * server instead ("6m") would be re-resolved per endpoint, against a clock
 * that has moved between requests.
 *
 * It lives in the URL so a filtered view survives a reload and can be handed
 * to someone else, and so the browser's back button steps through windows
 * the way it steps through pages.
 */

export type RangeKey = "30d" | "90d" | "6m" | "12m" | "2y" | "all"

export const RANGES: { key: RangeKey; label: string; days: number | null }[] = [
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
  { key: "6m", label: "Last 6 months", days: 182 },
  { key: "12m", label: "Last 12 months", days: 365 },
  { key: "2y", label: "Last 2 years", days: 730 },
  { key: "all", label: "All time", days: null },
]

/** All time is the default: the record is what it is, and a window that hides
 *  eight months of it by default is what made this necessary. */
export const DEFAULT_RANGE: RangeKey = "all"

export function rangeLabel(key: RangeKey): string {
  return RANGES.find((r) => r.key === key)?.label ?? "All time"
}

export function isRangeKey(value: string | null): value is RangeKey {
  return value !== null && RANGES.some((r) => r.key === value)
}

/** Absolute ISO bounds for a range key, or empty for all-time. Resolved at
 *  call time against one `now`, so every query in a render shares an edge. */
export function rangeParams(key: RangeKey, now: Date = new Date()): URLSearchParams {
  const params = new URLSearchParams()
  const entry = RANGES.find((r) => r.key === key)
  if (!entry || entry.days === null) return params
  const since = new Date(now.getTime() - entry.days * 86_400_000)
  params.set("since", since.toISOString())
  params.set("until", now.toISOString())
  return params
}

/** The range as a query-string fragment ready to append, "" when all-time. */
export function rangeQuery(key: RangeKey, now?: Date): string {
  const params = rangeParams(key, now)
  const text = params.toString()
  return text ? `?${text}` : ""
}
