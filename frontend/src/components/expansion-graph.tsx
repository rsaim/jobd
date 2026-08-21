/**
 * The expansion graph, live: a force-directed network that grows as the run
 * learns. The mailbox sits at the center; query families ring it; every
 * entity the harvest step teaches (a sender domain, an address, a thread)
 * pops in attached to its family, and the company that taught it appears
 * beside it. Watching this is watching the algorithm think — nodes arriving
 * IS the "seed and expand" mechanism, not a decoration of it.
 *
 * Canvas + d3-force: hundreds of nodes at 60fps without a DOM node each.
 * Colors resolve from the app's chart tokens at draw time so the graph obeys
 * theme switches. Identity is never color-alone: a legend names the three
 * families, and hover names the node (per-mark tooltip).
 *
 * Reduced motion: the simulation still lays out (layout is information),
 * but the entrance pulse is skipped and the sim runs at low alpha so nodes
 * settle without visible swirling.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force"

import type { EntityNode, QueryYield, ScrapeLive } from "@/lib/scrape-stream"

interface GraphNode extends SimulationNodeDatum {
  id: string
  label: string
  type: "account" | "family" | "entity" | "company"
  weight: number
  born: number
}

type GraphLink = SimulationLinkDatum<GraphNode>

const FAMILY_LABEL: Record<string, string> = {
  "seed:sent": "sent mail",
  "seed:ats": "ATS senders",
  "seed:inmail": "InMail relay",
  "expand:domain": "learned domains",
  "expand:address": "learned people",
  "expand:thread": "threads",
  "residual:direct": "direct sweep",
}

function familyOf(origin: string): string {
  if (origin.startsWith("seed:phrase")) return "seed:phrase"
  return origin
}

function familyLabel(family: string): string {
  return FAMILY_LABEL[family] ?? (family === "seed:phrase" ? "phrases" : family)
}

/** Fold the live feed into a stable node/link list. Pure — memoized. */
function buildGraph(
  yields: QueryYield[],
  entities: EntityNode[],
  account: string,
): { nodes: Omit<GraphNode, "x" | "y">[]; links: { source: string; target: string }[] } {
  const nodes = new Map<string, Omit<GraphNode, "x" | "y">>()
  const links: { source: string; target: string }[] = []
  nodes.set("account", {
    id: "account",
    label: account || "mailbox",
    type: "account",
    weight: 24,
    born: 0,
  })
  for (const y of [...yields].reverse()) {
    const family = familyOf(y.origin)
    const id = `family:${family}`
    const existing = nodes.get(id)
    if (existing) {
      existing.weight += y.fresh
    } else {
      nodes.set(id, {
        id,
        label: familyLabel(family),
        type: "family",
        weight: 6 + y.fresh,
        born: y.seq,
      })
      links.push({ source: "account", target: id })
    }
  }
  for (const e of entities) {
    if (e.kind === "thread") continue // threads are volume, not identity
    const id = `entity:${e.value}`
    if (nodes.has(id)) continue
    const family = e.kind === "domain" ? "family:expand:domain" : "family:expand:address"
    if (!nodes.has(family)) {
      nodes.set(family, {
        id: family,
        label: familyLabel(family.slice(7)),
        type: "family",
        weight: 6,
        born: e.seq,
      })
      links.push({ source: "account", target: family })
    }
    nodes.set(id, {
      id,
      label: e.value,
      type: "entity",
      weight: 5,
      born: e.seq,
    })
    links.push({ source: family, target: id })
    if (e.via) {
      const companyId = `company:${e.via}`
      if (!nodes.has(companyId)) {
        nodes.set(companyId, {
          id: companyId,
          label: e.via,
          type: "company",
          weight: 8,
          born: e.seq,
        })
      }
      links.push({ source: id, target: companyId })
    }
  }
  return { nodes: [...nodes.values()], links }
}

function cssColor(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name)
  return value.trim() || fallback
}

export function ExpansionGraph({ live }: { live: ScrapeLive }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const simRef = useRef<ReturnType<typeof forceSimulation<GraphNode>> | null>(null)
  const nodesRef = useRef<GraphNode[]>([])
  const [hover, setHover] = useState<{ x: number; y: number; node: GraphNode } | null>(
    null,
  )

  const graph = useMemo(
    () => buildGraph(live.yields, live.entities, live.accounts[0] ?? ""),
    [live.yields, live.entities, live.accounts],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches

    const width = wrap.clientWidth
    const height = wrap.clientHeight
    const dpr = window.devicePixelRatio || 1
    canvas.width = width * dpr
    canvas.height = height * dpr

    // Carry positions across rebuilds so existing nodes stay put and only
    // newcomers animate in (from their link parent's position).
    const previous = new Map(nodesRef.current.map((n) => [n.id, n]))
    const parentOf = new Map<string, string>()
    for (const l of graph.links) {
      parentOf.set(
        typeof l.target === "string" ? l.target : (l.target as GraphNode).id,
        typeof l.source === "string" ? l.source : (l.source as GraphNode).id,
      )
    }
    const nodes: GraphNode[] = graph.nodes.map((n) => {
      const old = previous.get(n.id)
      if (old) return Object.assign(old, { weight: n.weight })
      const parent = previous.get(parentOf.get(n.id) ?? "account")
      return {
        ...n,
        x: (parent?.x ?? width / 2) + (Math.random() - 0.5) * 24,
        y: (parent?.y ?? height / 2) + (Math.random() - 0.5) * 24,
        born: performance.now() as number,
      }
    })
    nodesRef.current = nodes
    const links: GraphLink[] = graph.links.map((l) => ({ ...l }))

    const radius = (n: GraphNode) =>
      n.type === "account"
        ? 14
        : n.type === "family"
          ? Math.min(22, 7 + Math.sqrt(n.weight) * 1.4)
          : n.type === "company"
            ? 6
            : 4

    const sim = forceSimulation<GraphNode>(nodes)
      .force(
        "link",
        forceLink<GraphNode, GraphLink>(links)
          .id((d) => d.id)
          .distance((l) => {
            const t = l.target as GraphNode
            return t.type === "family" ? 90 : t.type === "company" ? 28 : 46
          })
          .strength(0.5),
      )
      .force("charge", forceManyBody<GraphNode>().strength(-38))
      .force("x", forceX<GraphNode>(width / 2).strength(0.06))
      .force("y", forceY<GraphNode>(height / 2).strength(0.08))
      .force(
        "collide",
        forceCollide<GraphNode>().radius((d) => radius(d) + 3),
      )
      .alpha(reduced ? 0.3 : 0.9)
      .alphaDecay(reduced ? 0.12 : 0.035)
    simRef.current = sim

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const colors = {
      ink: cssColor("--foreground", "#20262e"),
      muted: cssColor("--muted-foreground", "#626b76"),
      border: cssColor("--border", "#e4e4de"),
      family: cssColor("--chart-2", "#4a90d9"),
      entity: cssColor("--chart-3", "#5b6bd9"),
      company: cssColor("--chart-4", "#c9922a"),
      surface: cssColor("--card", "#ffffff"),
    }

    const draw = () => {
      ctx.save()
      ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, width, height)
      ctx.lineWidth = 1
      ctx.strokeStyle = colors.border
      for (const l of links) {
        const s = l.source as GraphNode
        const t = l.target as GraphNode
        if (s.x == null || t.x == null) continue
        ctx.beginPath()
        ctx.moveTo(s.x!, s.y!)
        ctx.lineTo(t.x!, t.y!)
        ctx.stroke()
      }
      const now = performance.now()
      for (const n of nodes) {
        if (n.x == null) continue
        const r = radius(n)
        const age = now - n.born
        // entrance pulse: a brief ring, skipped under reduced motion
        if (!reduced && age < 700 && n.type !== "account") {
          const k = age / 700
          ctx.beginPath()
          ctx.arc(n.x!, n.y!, r + k * 14, 0, Math.PI * 2)
          ctx.strokeStyle =
            n.type === "company" ? colors.company : n.type === "family" ? colors.family : colors.entity
          ctx.globalAlpha = 1 - k
          ctx.stroke()
          ctx.globalAlpha = 1
        }
        ctx.beginPath()
        ctx.arc(n.x!, n.y!, r, 0, Math.PI * 2)
        ctx.fillStyle =
          n.type === "account"
            ? colors.ink
            : n.type === "family"
              ? colors.family
              : n.type === "company"
                ? colors.company
                : colors.entity
        ctx.fill()
        // 2px surface ring so overlapping marks stay separable
        ctx.lineWidth = 2
        ctx.strokeStyle = colors.surface
        ctx.stroke()
        ctx.lineWidth = 1
        if (n.type === "family" || n.type === "account" || n.type === "company") {
          ctx.font =
            "10px ui-monospace, SFMono-Regular, Menlo, monospace"
          ctx.fillStyle = colors.muted
          ctx.textAlign = "center"
          const label =
            n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label
          ctx.fillText(label, n.x!, n.y! + r + 11)
        }
      }
      ctx.restore()
    }

    sim.on("tick", draw)
    draw()

    const onMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const x = event.clientX - rect.left
      const y = event.clientY - rect.top
      let best: GraphNode | null = null
      let bestD = 14
      for (const n of nodes) {
        if (n.x == null) continue
        const d = Math.hypot(n.x! - x, n.y! - y)
        if (d < bestD + radius(n)) {
          best = n
          bestD = d
        }
      }
      setHover(best ? { x, y, node: best } : null)
    }
    canvas.addEventListener("mousemove", onMove)
    canvas.addEventListener("mouseleave", () => setHover(null))
    return () => {
      sim.stop()
      canvas.removeEventListener("mousemove", onMove)
    }
  }, [graph])

  const empty = graph.nodes.length <= 1

  return (
    <div ref={wrapRef} className="relative h-[340px] w-full">
      <canvas ref={canvasRef} className="size-full" style={{ width: "100%", height: "100%" }} />
      {empty && (
        <p className="text-muted-foreground absolute inset-0 flex items-center justify-center text-[13px]">
          The graph draws itself as the run discovers senders and companies.
        </p>
      )}
      {hover && (
        <div
          className="bg-popover text-popover-foreground pointer-events-none absolute z-10 rounded-md border px-2 py-1 font-mono text-[11px] shadow-sm"
          style={{ left: hover.x + 10, top: hover.y + 10 }}
        >
          {hover.node.type === "family"
            ? `${hover.node.label} · +${Math.max(0, Math.round(hover.node.weight - 6))} found`
            : hover.node.label}
        </div>
      )}
      <div className="text-muted-foreground absolute right-2 bottom-1 flex items-center gap-3 font-mono text-[10px]">
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-full bg-[var(--chart-2)]" />
          query family
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-full bg-[var(--chart-3)]" />
          learned sender
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-full bg-[var(--chart-4)]" />
          company
        </span>
      </div>
    </div>
  )
}
