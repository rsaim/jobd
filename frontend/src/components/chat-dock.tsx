/**
 * The floating chat dock — a LinkedIn/Facebook-style conversation window
 * pinned to the bottom-right corner, replacing the fixed 26rem third column
 * the shell used to reserve for chat.
 *
 * Two states, both always rendered: a collapsed launcher bar sitting flush
 * with the bottom edge, and an expanded window that grows upward from the
 * same corner. The expanded panel is hidden with CSS rather than unmounted
 * so the assistant-ui runtime (and any in-flight stream) survives a
 * minimize — company threads persist via lib/chat-history.ts anyway, but
 * the global thread lives only in this mount.
 *
 * The page never gets an overlay or a backdrop: the record stays fully
 * usable underneath, which is the whole point of a dock over a modal.
 * Every page also stays addressable (M7 gate 1) — the dock follows the
 * URL, it never owns it.
 */

import { useCallback, useEffect, useState } from "react"
import { ChevronUp, MessageSquareOff, Sparkles } from "lucide-react"

import { ChatPanel } from "@/components/chat-panel"

const STORAGE_KEY = "jobd-chat-dock"

/** What the expanded window shows instead of the panel when no model is
 *  configured — same information company-summary.tsx's own notice gives,
 *  since both gate on the same `chrome.chat_model` and used to both just
 *  disappear silently. */
function ChatUnavailableNotice() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <span
        aria-hidden
        className="bg-background text-muted-foreground ring-border grid size-10 place-items-center rounded-full ring-1"
      >
        <MessageSquareOff className="size-4.5" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-semibold">Chat isn't configured</p>
        <p className="text-muted-foreground text-[12.5px] leading-relaxed">
          No model is set up on this server. Set{" "}
          <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
            OPENROUTER_API_KEY
          </code>{" "}
          and{" "}
          <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
            JOBD_CHAT_MODEL
          </code>{" "}
          and restart the server to turn this on.
        </p>
      </div>
    </div>
  )
}

export function ChatDock({
  model,
  models,
  companyId,
}: {
  model: string | null
  models: string[]
  companyId?: string
}) {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "open"
    } catch {
      return false
    }
  })

  const setAndRemember = useCallback((next: boolean) => {
    setOpen(next)
    try {
      localStorage.setItem(STORAGE_KEY, next ? "open" : "min")
    } catch {
      /* private mode; the state still applies for this visit */
    }
  }, [])

  // Esc minimizes — but only when nothing above the dock (a dialog, the
  // command palette) is going to consume it first; those stop propagation
  // at the document level before this listener sees the event.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAndRemember(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, setAndRemember])

  return (
    <div className="fixed right-4 bottom-0 z-40 max-sm:right-2">
      {/* Collapsed launcher — LinkedIn's docked "Messaging" bar. */}
      <button
        type="button"
        aria-expanded={open}
        aria-controls="chat-dock-window"
        onClick={() => setAndRemember(true)}
        className={
          "bg-sidebar hover:bg-accent border-border/80 flex w-72 cursor-pointer items-center gap-2.5 rounded-t-xl border border-b-0 px-3 py-2.5 shadow-[0_-2px_16px_-4px_rgb(0_0_0/0.15)] transition-[opacity,transform] motion-reduce:transition-none " +
          (open ? "pointer-events-none translate-y-2 opacity-0" : "translate-y-0 opacity-100")
        }
      >
        <span
          aria-hidden
          className="from-primary/15 to-primary/5 text-primary ring-primary/15 grid size-6 place-items-center rounded-md bg-gradient-to-b ring-1"
        >
          <Sparkles className="size-3.5" />
        </span>
        <span className="text-sm font-semibold">Ask jobd</span>
        <ChevronUp className="text-muted-foreground ml-auto size-4" />
      </button>

      {/* Expanded window — grows upward from the same corner. Kept mounted
          while minimized (see module docstring). */}
      <div
        id="chat-dock-window"
        className={
          "bg-sidebar border-border/80 absolute right-0 bottom-0 flex h-[min(37.5rem,calc(100svh-4.5rem))] w-[min(24rem,calc(100vw-1rem))] origin-bottom-right flex-col overflow-hidden rounded-t-xl border border-b-0 shadow-2xl transition-[opacity,transform] motion-reduce:transition-none " +
          (open
            ? "translate-y-0 scale-100 opacity-100"
            : "pointer-events-none invisible translate-y-3 scale-[0.98] opacity-0")
        }
      >
        {model ? (
          <ChatPanel
            model={model}
            models={models}
            companyId={companyId}
            onMinimize={() => setAndRemember(false)}
          />
        ) : (
          <ChatUnavailableNotice />
        )}
      </div>
    </div>
  )
}
