/**
 * The selected range, shared by every page and stored in the URL.
 *
 * A context rather than a prop threaded through eight pages: the range is a
 * property of the whole view, and passing it by hand would let one page
 * forget it and quietly report on a different window than the header claims.
 */

import { createContext, useContext, useCallback, type ReactNode } from "react"
import { useSearchParams } from "react-router-dom"

import { DEFAULT_RANGE, isRangeKey, rangeQuery, type RangeKey } from "@/lib/range"

const RangeContext = createContext<{
  range: RangeKey
  setRange: (key: RangeKey) => void
  /** Query fragment for the current range — "" when all-time. */
  query: string
}>({ range: DEFAULT_RANGE, setRange: () => {}, query: "" })

export function RangeProvider({ children }: { children: ReactNode }) {
  const [params, setParams] = useSearchParams()
  const raw = params.get("range")
  const range: RangeKey = isRangeKey(raw) ? raw : DEFAULT_RANGE

  const setRange = useCallback(
    (key: RangeKey) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          // All time is the default, so it stays out of the URL rather than
          // pinning a redundant ?range=all on every link.
          if (key === DEFAULT_RANGE) next.delete("range")
          else next.set("range", key)
          return next
        },
        { replace: false },
      )
    },
    [setParams],
  )

  return (
    <RangeContext.Provider value={{ range, setRange, query: rangeQuery(range) }}>
      {children}
    </RangeContext.Provider>
  )
}

export function useRange() {
  return useContext(RangeContext)
}
