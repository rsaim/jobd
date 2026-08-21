/**
 * The chat panel — assistant-ui's `Thread` (github.com/assistant-ui/assistant-ui,
 * 11.6k stars, the most-starred *embeddable* React chat component library;
 * mckaywrigley/chatbot-ui has more stars but is a standalone Next.js+Supabase
 * app, not a component — it cannot be "injected" into an existing SPA
 * without an iframe, which would lose the shared data-context that is the
 * whole point of this panel reading jobd's own record) over a hand-written
 * `ChatModelAdapter` (lib/chat-runtime.ts) that streams jobd's own
 * `/api/chat` SSE endpoint.
 *
 * Always mounted, never a drawer: a persistent column in the shell's grid
 * (components/shell.tsx), not a `Sheet` overlay. Every page stays
 * addressable underneath it regardless (M7 gate 1) — that property never
 * depended on the panel being collapsible.
 *
 * A mutation proposal still renders as a real button calling the exact
 * endpoint the corresponding page's own control calls (SECURITY.md §4) —
 * that hasn't changed, only what renders it has. `propose_*` tool calls get
 * a custom `ToolFallback`; every other tool call (list_companies,
 * run_sql, ...) uses assistant-ui's own default, unmodified.
 *
 * Replying here is model-assisted: the model calls `propose_draft_reply`, a
 * human clicks Apply to actually write the Gmail draft (SECURITY.md §4, I1),
 * and this component renders a second, separate Send button for the draft
 * that comes back — sending is never reachable from the first click alone.
 * The company page also has its own reply box now (reply-box.tsx) for
 * writing one by hand, same two-step draft-then-send underneath — this
 * panel isn't the only way to reach it, just the one where a model helps
 * write it.
 *
 * On a company page, this panel doesn't generate its own summary (that
 * used to run twice — once here, once on the page, a real duplicate LLM
 * call users could see side by side). It waits on the page's copy
 * (company-summary.tsx) and, once ready, feeds it into one auto-fired turn
 * suggesting a next step — see buildSuggestionPrompt below.
 *
 * A company's thread persists across visits (lib/chat-history.ts,
 * localStorage — the server still keeps none of it, see that file's
 * docstring) and is keyed by company id: switching companies swaps in that
 * company's own conversation rather than continuing whatever was on
 * screen, and Reset actually throws one away. `ChatThread` below is the
 * remount boundary that makes that happen — assistant-ui's local runtime
 * has no "swap the history adapter mid-life" call, so a fresh mount (via
 * `key`, in `ChatPanel`) is what re-runs `history.load()` for the new
 * company.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"
import { Check, ChevronDown, Loader2, RotateCcw, Send, Sparkles } from "lucide-react"
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react"
import { useQueryClient } from "@tanstack/react-query"

import { Thread } from "@/components/assistant-ui/thread"
import { ToolFallback as DefaultToolFallback } from "@/components/assistant-ui/tool-fallback"
import { createJobdChatModel, type Proposal } from "@/lib/chat-runtime"
import { clearHistory, createCompanyHistoryAdapter, hasSavedHistory } from "@/lib/chat-history"
import { useCompanySummary, useSendReply } from "@/lib/api"
import type { OutboundDraft } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import type { ToolCallMessagePartComponent } from "@assistant-ui/react"

/** `propose_*` tool calls carry the `Proposal` object as their `result`
 *  (chat-runtime.ts) — everything else falls through to the library's own
 *  ToolFallback, untouched. */
const ProposalToolFallback: ToolCallMessagePartComponent = (part) => {
  const client = useQueryClient()
  const sendReply = useSendReply()
  const [draft, setDraft] = useState<OutboundDraft | null>(null)

  if (!part.toolName.startsWith("propose_") || !part.result) {
    return <DefaultToolFallback {...part} />
  }
  const proposal = part.result as Proposal
  const isDraftReply = proposal.action === "draft_reply"

  const apply = async () => {
    const response = await fetch(
      proposal.endpoint.startsWith("/api") ? proposal.endpoint : `/api${proposal.endpoint}`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(proposal.fields),
      },
    )
    const result = await response.json().catch(() => ({ ok: false, message: "Failed." }))
    toast[result.ok ? "success" : "error"](result.message ?? "Done.")
    if (isDraftReply && result.ok && result.draft) setDraft(result.draft)
    void client.invalidateQueries()
  }

  const send = () => {
    if (!draft) return
    sendReply.mutate(draft.id, {
      onSuccess: (r) => {
        if (r.ok && r.draft) {
          setDraft(r.draft)
          toast.success("Sent.")
        } else {
          toast.error(r.message ?? "Could not send this.")
        }
      },
      onError: (error) => toast.error((error as Error).message),
    })
  }

  return (
    // A proposal is the one thing in this panel that writes to the record,
    // so it is the one thing drawn in the signal hue: tinted plate, signal
    // edge, and the action inside it.
    <div className="bg-primary/[0.06] border-primary/25 my-1 flex flex-col gap-2 rounded-lg border p-3 text-[13px]">
      <span className="font-semibold">{proposal.summary}</span>
      {proposal.blast_radius && (
        <span className="text-muted-foreground text-[11.5px]">{proposal.blast_radius}</span>
      )}
      {!draft ? (
        <Button size="sm" variant="outline" className="w-fit" onClick={() => void apply()}>
          <Check /> Apply
        </Button>
      ) : draft.status === "sent" ? (
        <span className="text-[11.5px] font-medium">Sent.</span>
      ) : (
        <div className="flex items-center gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button size="sm" disabled={sendReply.isPending}>
                {sendReply.isPending ? <Loader2 className="animate-spin" /> : <Send />}
                {sendReply.isPending ? "Sending…" : "Send"}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Send this email?</AlertDialogTitle>
                <AlertDialogDescription>
                  Going to {draft.recipients.join(", ")}. This can't be undone — Gmail
                  will keep the draft to edit, but a sent message isn't recallable.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={send}>Send</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <span className="text-muted-foreground text-[11px]">
            Drafted — check Gmail, or send from here.
          </span>
        </div>
      )}
    </div>
  )
}

/** The already-generated mail summary (company-summary.tsx — this is the
 *  only thing that ever calls the generate endpoint) fed straight in as
 *  the turn's own text, not re-derived. The model has everything it needs
 *  right there; it doesn't need to re-read every message via tools to
 *  answer this, which is what a "go research this yourself" prompt did
 *  instead (live-caught: 300+ tool calls in one turn). It can still reach
 *  for a tool (propose_draft_reply, or the web — on by default,
 *  api.py's chat_turn) when the summary alone doesn't answer it.
 *
 *  Explicit length/shape constraints, not just "what's the next step" —
 *  live-tested without them, the model re-narrated the whole summary back
 *  before getting to its point, which is redundant with the summary card
 *  sitting right above it (the fed-in text itself, visible in this same
 *  thread) and just makes the actual answer slower to reach. */
function buildSuggestionPrompt(summaryMarkdown: string): string {
  return (
    "Here is this company's mail summary:\n\n" +
    summaryMarkdown +
    "\n\nDon't repeat or re-summarize any of the above — I can already see " +
    "it. In 2-3 sentences: what's the next step, and why. If a reply is " +
    "warranted, draft one with propose_draft_reply; if not, say so in one " +
    "line and stop."
  )
}

/** One mounted runtime + thread. Remounted whenever `ChatPanel` below
 *  changes its `key` (company switch, or Reset) — that remount is what
 *  re-runs the history adapter's `load()` for the new company and gives a
 *  clean `suggestedFor`/`suggesting` state, rather than this component
 *  trying to swap its own history mid-life (assistant-ui's local runtime
 *  has no call for that). */
function ChatThread({
  modelRef,
  companyId,
}: {
  modelRef: { current: string }
  companyId?: string
}) {
  const chatModel = useMemo(() => createJobdChatModel(modelRef), [])
  const runtime = useLocalRuntime(
    chatModel,
    companyId
      ? { adapters: { history: createCompanyHistoryAdapter(companyId) } }
      : undefined,
  )

  // Waits on the company page's own summary generation (company-summary.tsx
  // — the sole caller of the generate endpoint now, no duplicate copy here)
  // and, once it's ready, feeds it straight into one auto-fired chat turn
  // asking for a next step. Skipped entirely when this company already has
  // a saved conversation (hasSavedHistory) — restoring one should show
  // what's there, not append a fresh "next step" turn on top of it every
  // time the page is reopened; a genuinely fresh start only happens via
  // Reset, which clears history before remounting this component.
  const { data: summaryData } = useCompanySummary(companyId ?? "")
  const summary = companyId ? (summaryData?.summary ?? null) : null
  const summaryReady = Boolean(summary && !summary.stale)
  const suggestedFor = useRef<string | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  useEffect(() => {
    if (!companyId || !summaryReady || suggestedFor.current === companyId) return
    suggestedFor.current = companyId
    if (hasSavedHistory(companyId)) return
    setSuggesting(true)
    runtime.thread.append(buildSuggestionPrompt(summary!.markdown))
    const unsubscribe = runtime.thread.subscribe(() => {
      if (!runtime.thread.getState().isRunning) {
        setSuggesting(false)
        unsubscribe()
      }
    })
    return unsubscribe
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, summaryReady, runtime])

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {suggesting && (
        <div className="text-muted-foreground flex items-center gap-2 border-b px-3 py-2 text-[13px]">
          <Loader2 className="size-3.5 animate-spin" />
          Generating suggested next step…
        </div>
      )}
      <div className="min-h-0 flex-1">
        <Thread components={{ ToolFallback: ProposalToolFallback }} />
      </div>
    </AssistantRuntimeProvider>
  )
}

export function ChatPanel({
  model,
  models,
  companyId,
  onMinimize,
}: {
  model: string
  /** The picker's options. Just `[model]` when the operator hasn't opted
   *  into the curated allowlist (see api.py's `_chat_models`) — the picker
   *  still renders, with nothing to switch to. */
  models: string[]
  /** Set when the current URL is a company page — once its summary is
   *  ready, this panel auto-suggests a next step (including a reply, via
   *  propose_draft_reply) from it, no click needed. Also what scopes the
   *  persisted conversation (lib/chat-history.ts): a page with no
   *  companyId (home, /messages, ...) gets a plain, unsaved thread,
   *  unchanged from before this existed. */
  companyId?: string
  /** Set by the floating dock (chat-dock.tsx): renders a minimize control
   *  in the header. Absent when the panel is hosted somewhere that isn't
   *  collapsible. */
  onMinimize?: () => void
}) {
  const [activeModel, setActiveModel] = useState(model)
  const modelRef = useRef(activeModel)
  modelRef.current = activeModel

  // Bumped by Reset — folded into ChatThread's `key` below so clearing
  // storage and actually getting a blank thread happen together. Owned up
  // here, not inside ChatThread, so it survives that remount instead of
  // resetting itself back to 0 on the next one.
  const [resetNonce, setResetNonce] = useState(0)

  const reset = () => {
    if (!companyId) return
    if (!window.confirm("Clear this company's chat history? This can't be undone."))
      return
    clearHistory(companyId)
    setResetNonce((n) => n + 1)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="bg-background/60 flex items-center gap-2 border-b px-3 py-2 backdrop-blur-sm">
        <span
          aria-hidden
          className="from-primary/15 to-primary/5 text-primary ring-primary/15 grid size-6 place-items-center rounded-md bg-gradient-to-b ring-1"
        >
          <Sparkles className="size-3.5" />
        </span>
        <span className="text-sm font-semibold whitespace-nowrap">Ask jobd</span>
        <div className="ml-auto flex items-center gap-1.5">
          {companyId && (
            <Button
              size="icon-sm"
              variant="ghost"
              title="Clear this company's chat history and start over"
              onClick={reset}
            >
              <RotateCcw className="size-3.5" />
            </Button>
          )}
          {models.length > 1 ? (
            <Select value={activeModel} onValueChange={setActiveModel}>
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
          ) : (
            <span className="text-muted-foreground font-mono text-[10.5px]">{model}</span>
          )}
          {onMinimize && (
            <Button
              size="icon-sm"
              variant="ghost"
              title="Minimize"
              aria-label="Minimize chat"
              onClick={onMinimize}
            >
              <ChevronDown className="size-4" />
            </Button>
          )}
        </div>
      </div>
      <ChatThread
        key={`${companyId ?? "global"}-${resetNonce}`}
        modelRef={modelRef}
        companyId={companyId}
      />
    </div>
  )
}
