/**
 * Live state for one scrape run, folded from the SSE feed at
 * `/api/scrape/stream` (shapes: `scrape/events.py` via `web/api.py`).
 *
 * One reducer owns everything the page shows — stations, counters, yields,
 * facts — so a reconnect can replay from the last seen `seq` and arrive at
 * the same state. The EventSource lifecycle lives in the hook: it opens
 * when a run is (or becomes) live, follows it to `done`, and closes; the
 * server ends the stream after the terminal event.
 */

import { useCallback, useEffect, useReducer, useRef } from "react"

export type StationName =
  | "plan"
  | "list"
  | "fetch"
  | "classify"
  | "audit"
  | "harvest"
  | "residual"
  | "report"

export const STATIONS: StationName[] = [
  "plan",
  "list",
  "fetch",
  "classify",
  "audit",
  "harvest",
  "residual",
  "report",
]

export interface QueryYield {
  seq: number
  origin: string
  q: string
  matched: number
  fresh: number
  hop: number
}

export interface Fact {
  seq: number
  text: string
}

export interface EntityNode {
  seq: number
  kind: "domain" | "address" | "thread"
  value: string
  via: string
  hop: number
}

export interface ScrapeLive {
  phase: "idle" | "running" | "done" | "error"
  station: StationName | null
  hop: number
  counters: Record<string, number>
  yields: QueryYield[]
  facts: Fact[]
  entities: EntityNode[]
  accounts: string[]
  windowDays: number | null
  startedAt: string | null
  fetchProgress: { fetched: number; total: number } | null
  error: string | null
  lastSeq: number
}

export const EMPTY: ScrapeLive = {
  phase: "idle",
  station: null,
  hop: 0,
  counters: {},
  yields: [],
  facts: [],
  entities: [],
  accounts: [],
  windowDays: null,
  startedAt: null,
  fetchProgress: null,
  error: null,
  lastSeq: 0,
}

/** Which station each event kind proves we are at. */
const STATION_OF: Record<string, StationName> = {
  run_started: "plan",
  query_yield: "list",
  fetch_progress: "fetch",
  classify_progress: "classify",
  audit_started: "audit",
  audit_done: "audit",
  expansion: "harvest",
  expansion_capped: "harvest",
  residual_started: "residual",
  done: "report",
}

type Frame = { kind: string; seq?: number } & Record<string, unknown>

function fold(state: ScrapeLive, frame: Frame): ScrapeLive {
  const seq = typeof frame.seq === "number" ? frame.seq : state.lastSeq
  const next: ScrapeLive = {
    ...state,
    lastSeq: Math.max(state.lastSeq, seq),
    station: STATION_OF[frame.kind] ?? state.station,
  }
  switch (frame.kind) {
    case "run_started":
      return {
        ...EMPTY,
        phase: "running",
        station: "plan",
        lastSeq: next.lastSeq,
        accounts: (frame.accounts as string[]) ?? [],
        windowDays: (frame.window_days as number | null) ?? null,
        startedAt: (frame.started_at as string) ?? null,
      }
    case "query_yield":
      return {
        ...next,
        phase: "running",
        hop: (frame.hop as number) ?? next.hop,
        yields: [
          {
            seq,
            origin: String(frame.origin ?? ""),
            q: String(frame.q ?? ""),
            matched: Number(frame.matched ?? 0),
            fresh: Number(frame.new ?? 0),
            hop: Number(frame.hop ?? 0),
          },
          ...next.yields,
        ].slice(0, 60),
      }
    case "fetch_progress":
      return {
        ...next,
        phase: "running",
        fetchProgress: {
          fetched: Number(frame.fetched ?? 0),
          total: Number(frame.total ?? 0),
        },
      }
    case "counters":
    case "classify_progress": {
      const merged = { ...next.counters }
      for (const [key, value] of Object.entries(frame)) {
        if (typeof value === "number" && key !== "seq" && key !== "ts" && key !== "hop") {
          merged[key] = value
        }
      }
      return { ...next, phase: "running", counters: merged }
    }
    case "expansion":
      return { ...next, phase: "running", hop: Number(frame.hop ?? next.hop) }
    case "entity":
      return {
        ...next,
        entities: [
          ...next.entities,
          {
            seq,
            kind: (frame.entity_kind as EntityNode["kind"]) ?? "domain",
            value: String(frame.value ?? ""),
            via: String(frame.via ?? ""),
            hop: Number(frame.hop ?? 0),
          },
        ].slice(-400),
      }
    case "fact":
      return {
        ...next,
        facts: [{ seq, text: String(frame.text ?? "") }, ...next.facts].slice(0, 60),
      }
    case "error":
      return { ...next, error: String(frame.message ?? "failed") }
    case "done":
      return {
        ...next,
        phase: next.error ? "error" : "done",
        station: "report",
        counters: { ...next.counters, ...((frame.counters as Record<string, number>) ?? {}) },
        fetchProgress: null,
      }
    default:
      return next
  }
}

/** Follow the current run (if any). `kick` after POSTing /scrape/start. */
export function useScrapeStream(running: boolean) {
  const [live, dispatch] = useReducer(fold, EMPTY)
  const sourceRef = useRef<EventSource | null>(null)
  const seqRef = useRef(0)
  seqRef.current = live.lastSeq

  const open = useCallback(() => {
    if (sourceRef.current) return
    const source = new EventSource(`/api/scrape/stream?after=${seqRef.current}`)
    sourceRef.current = source
    source.onmessage = (message) => {
      try {
        dispatch(JSON.parse(message.data) as Frame)
      } catch {
        // a malformed frame is dropped, never fatal to the stream
      }
    }
    source.onerror = () => {
      // The server closes the stream after `done` (and when idle); the
      // browser fires error on close either way. Drop the handle — the
      // effect below reopens it while a run is still live.
      source.close()
      sourceRef.current = null
    }
  }, [])

  useEffect(() => {
    if (running || live.phase === "running") open()
    return () => {
      // keep the source across re-renders; closed only by unmount below
    }
  }, [running, live.phase, open])

  useEffect(
    () => () => {
      sourceRef.current?.close()
      sourceRef.current = null
    },
    [],
  )

  return { live, kick: open }
}
