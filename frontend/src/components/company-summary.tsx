/**
 * A company's mail, summarized — read-only, streamed. The company page's own
 * panel, auto-starting on mount. The single generation this produces is also
 * what the chat panel waits on and feeds into its own "suggested next step"
 * turn (chat-panel.tsx) rather than generating a second copy itself — this
 * component is the only thing that ever calls the generate endpoint.
 *
 * Read-only on purpose: this used to also carry an editable To/Subject/body
 * reply composer, wired straight to Sender.draft/send. Replying now lives
 * in the chat window instead — the model drafts one via the propose_*
 * pattern (chat-panel.tsx's ProposalToolFallback), a human's own click on
 * an Apply button is still the only thing that ever writes a Gmail draft
 * (SECURITY.md §4, I1) or sends it. This panel only ever reads.
 */

import { useEffect, useRef, useState } from "react"
import { RefreshCw, Globe, Sparkles, TriangleAlert } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { useQueryClient } from "@tanstack/react-query"

import { useChrome, useCompanySummary } from "@/lib/api"
import { streamCompanySummary } from "@/lib/summary-stream"
import { fmtStamp } from "@/lib/record"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/** The model always writes a "## Suggested reply" section when one's
 *  warranted (domain/summary.py's PROMPT) — this page never shows it
 *  (replying lives in chat now, fed from the same generation — see the
 *  module docstring). Always the last section, so a plain split at its
 *  heading is enough. */
function stripSuggestedReply(markdown: string): string {
  return markdown.split(/\n#{1,3}\s*Suggested reply/i)[0].trimEnd()
}

export function CompanySummary({
  companyId,
  autoStart = false,
}: {
  companyId: string
  /** Start streaming as soon as the cache is known stale/empty, no click
   *  needed. */
  autoStart?: boolean
}) {
  const { data: chrome, isPending: chromePending } = useChrome()
  const { data: cached, isPending: cacheLoading } = useCompanySummary(companyId)
  const client = useQueryClient()
  const [model, setModel] = useState<string | undefined>(undefined)
  const [web, setWeb] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [streamedText, setStreamedText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const startedFor = useRef<string | null>(null)
  const models = chrome?.chat_models ?? []

  const cachedSummary = cached?.summary ?? null
  const rawText = streamedText ?? cachedSummary?.markdown ?? null
  const text = rawText ? stripSuggestedReply(rawText) : rawText

  async function run() {
    setStreaming(true)
    setError(null)
    setStreamedText("")
    const result = await streamCompanySummary(
      companyId,
      { model, web },
      (chunk) => setStreamedText((current) => (current ?? "") + chunk),
    )
    setStreaming(false)
    if (result.done === "error") setError(result.error ?? "Could not generate a summary.")
    else if (result.done === "busy") {
      // Someone else (the other surface — this company's own page and its
      // chat quick-action can both auto-start on the same load) is already
      // generating. Nothing to show yet, but the winner will have written
      // the cache row by the time it finishes — poll a few times rather
      // than refetching once: a single fixed-delay retry raced ahead of a
      // real (multi-second) generation and lost, live-tested — this surface
      // stayed on "No summary yet" forever even though the other one had
      // finished seconds later. Bounded (6 tries, ~12s) so a genuinely dead
      // winner doesn't poll forever either.
      setStreamedText(null)
      setError(null)
      let attempts = 0
      const poll = () => {
        attempts += 1
        void client.invalidateQueries({ queryKey: ["company-summary", companyId] })
        if (attempts < 6) setTimeout(poll, 2000)
      }
      setTimeout(poll, 1500)
    } else if (!result.text.trim()) {
      setStreamedText(null) // nothing to summarize
    } else {
      // Done, cache row written server-side — invalidate so any other
      // reader of this same query key (the chat panel, which no longer
      // generates its own copy — it waits on this one and feeds the result
      // in as a chat turn, chat-panel.tsx) picks it up without polling.
      void client.invalidateQueries({ queryKey: ["company-summary", companyId] })
    }
  }

  // Auto-start once per company, only when the cache is known (not merely
  // pending) and turns out empty or stale — never races the initial GET.
  //
  // The ref is claimed only when a run actually starts, not on every pass
  // that reaches this point. It used to be set before the empty/stale test,
  // so the first effect that ran with the cache settled burned the slot for
  // this company whether or not it generated anything — and because the
  // component returns null while `chrome` is still loading, that pass
  // routinely happened before there was a model to generate with. The card
  // then sat on "No summary yet" forever, since no later pass could retry.
  //
  // `chromePending` is a dependency for the same reason: without a model the
  // stream would 404, so waiting for chrome is part of "can we start", not a
  // render-only concern.
  useEffect(() => {
    if (!autoStart || chromePending || !chrome?.chat_model) return
    if (cacheLoading || streaming || startedFor.current === companyId) return
    if (cachedSummary && !cachedSummary.stale) return
    startedFor.current = companyId
    void run()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, chromePending, chrome?.chat_model, cacheLoading, companyId, cachedSummary])

  // Silent while chrome is still loading (avoids a one-frame flash of the
  // "not configured" notice on every page load); once it's actually loaded
  // and empty, say so — this used to be a bare `return null`, which reads
  // identically to a bug ("summary just isn't there") as it does to the
  // real cause (JOBD_CHAT_MODEL/OPENROUTER_API_KEY unset on the server).
  if (chromePending) return null
  if (!chrome?.chat_model) {
    return (
      <div className="panel bg-muted/30 rounded-xl border border-dashed p-4 text-[13px]">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <span
            aria-hidden
            className="bg-background text-muted-foreground ring-border grid size-6 place-items-center rounded-md ring-1"
          >
            <TriangleAlert className="size-3.5" />
          </span>
          Summary isn't configured
        </div>
        <p className="text-muted-foreground mt-2">
          No model is set up on this server. Set{" "}
          <code className="bg-background rounded px-1 py-0.5 font-mono text-[11px]">
            OPENROUTER_API_KEY
          </code>{" "}
          and{" "}
          <code className="bg-background rounded px-1 py-0.5 font-mono text-[11px]">
            JOBD_CHAT_MODEL
          </code>{" "}
          and restart the server to turn this on.
        </p>
      </div>
    )
  }

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="flex items-center gap-2 text-sm font-semibold">
        <span
          aria-hidden
          className="from-primary/15 to-primary/5 text-primary ring-primary/15 grid size-6 place-items-center rounded-md bg-gradient-to-b ring-1"
        >
          <Sparkles className="size-3.5" />
        </span>
        Summary
      </span>
      <div className="flex items-center gap-2">
        {models.length > 1 && (
          <Select value={model ?? chrome.chat_model ?? ""} onValueChange={setModel}>
            <SelectTrigger size="sm" className="h-7 w-auto font-mono text-[10.5px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem key={m} value={m} className="font-mono text-[10.5px]">
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button
          size="sm"
          variant={web ? "default" : "outline"}
          className="h-7 gap-1.5 text-xs"
          title="Let the model search the web while generating — real cost/latency per run, off by default"
          onClick={() => setWeb((v) => !v)}
        >
          <Globe className="size-3.5" />
          Web
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-7 gap-1.5 text-xs"
          disabled={streaming}
          onClick={() => void run()}
        >
          <RefreshCw className={streaming ? "size-3 animate-spin" : "size-3"} />
          {text ? "Regenerate" : "Generate"}
        </Button>
      </div>
    </div>
  )

  const body = error ? (
    <p className="text-destructive text-[12.5px]">{error}</p>
  ) : text ? (
    <>
      <div className="md max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        {streaming && <span className="animate-pulse">▍</span>}
      </div>
      {!streaming && (
        <p className="text-muted-foreground mt-2 border-t pt-2 text-[11px]">
          {(streamedText !== null ? model || chrome.chat_model : cachedSummary?.model) ?? chrome.chat_model}
          {" · "}
          {streamedText !== null ? "just now" : cachedSummary && fmtStamp(cachedSummary.created_at)}
        </p>
      )}
    </>
  ) : streaming ? (
    <p className="text-muted-foreground animate-pulse">Reading this company's mail…</p>
  ) : (
    <p className="text-muted-foreground">
      No summary yet. One model call reads this company's whole mail chain and writes
      back a headline, a short timeline, and anything that looks unrelated.
    </p>
  )

  return (
    <div className="panel bg-card rounded-xl border p-4 text-[13px]">
      {header}
      <div className="mt-3">{body}</div>
    </div>
  )
}
