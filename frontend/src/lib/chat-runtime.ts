/**
 * Bridges jobd's own `/api/chat` SSE endpoint to assistant-ui's `Thread`.
 *
 * All tool execution happens server-side (services/chat.py's read-only
 * transaction, tools/registry.py) — this adapter never calls a tool itself,
 * it only translates the events the backend already decided into the
 * `ThreadAssistantMessagePart[]` shape `<Thread>` renders. That split is
 * why `run()` ignores `options.context`/tools entirely: there is nothing
 * here for the client to execute (docs/chat-and-summaries.md §2).
 *
 * A `propose_*` tool call's `result` is the `Proposal` object itself
 * (SECURITY.md §4) — `ToolFallback` in chat-panel.tsx renders that
 * specially (an Apply button posting to the proposal's own endpoint);
 * every other tool call falls through to assistant-ui's default
 * `ToolFallback` (collapsible args/result).
 */

import type { ChatModelAdapter, ChatModelRunOptions, ThreadAssistantMessagePart } from "@assistant-ui/react"

export interface Proposal {
  action: string
  summary: string
  endpoint: string
  fields: Record<string, string>
  blast_radius?: string | null
}

/** Reads the *current* model at call time rather than closing over a stale
 *  value — the model picker mutates this ref directly, so switching models
 *  mid-thread takes effect on the next message without recreating the
 *  runtime (which would otherwise drop assistant-ui's own message state). */
export function createJobdChatModel(modelRef: { current: string }): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }: ChatModelRunOptions) {
      // Only role + text ever leaves the browser — a tool call's retrieved
      // content is never part of history (§4, "retrieved bodies are never
      // re-sent"). The server re-filters this same way independently; this
      // is the client's half of that promise, not the only one.
      const history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({
          role: m.role,
          text: m.content
            .filter((p): p is { type: "text"; text: string } => p.type === "text")
            .map((p) => p.text)
            .join(""),
        }))
      // The message just appended by the composer is the last one; the
      // rest is prior turns.
      const last = history.at(-1)
      const priorHistory = history.slice(0, -1)

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          message: last?.text ?? "",
          history: priorHistory,
          url_context: window.location.pathname + window.location.search,
          model: modelRef.current,
        }),
        signal: abortSignal,
      })
      if (!response.ok || !response.body) {
        yield {
          content: [{ type: "text", text: `Error: HTTP ${response.status}` }],
        }
        return
      }

      let text = ""
      const toolCalls = new Map<string, ThreadAssistantMessagePart & { type: "tool-call" }>()
      let toolSeq = 0
      const order: string[] = [] // preserves first-seen order for content[]

      const buildContent = (): ThreadAssistantMessagePart[] => {
        const parts: ThreadAssistantMessagePart[] = []
        if (text) parts.push({ type: "text", text })
        for (const id of order) {
          const call = toolCalls.get(id)
          if (call) parts.push(call)
        }
        return parts
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split("\n\n")
        buffer = frames.pop() ?? ""
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue
          let event: Record<string, any>
          try {
            event = JSON.parse(frame.slice(6))
          } catch {
            continue
          }

          if (event.type === "textdelta") {
            text += event.text
            yield { content: buildContent() }
          } else if (event.type === "toolcall") {
            const id = `${event.name}-${toolSeq++}`
            order.push(id)
            toolCalls.set(id, {
              type: "tool-call",
              toolCallId: id,
              toolName: event.name,
              args: event.args ?? {},
              argsText: JSON.stringify(event.args ?? {}),
            })
            yield { content: buildContent() }
          } else if (event.type === "toolresult") {
            // Matched by name against the most recent unmatched call of
            // that name — the SSE stream carries no call/result id pairing
            // beyond name + arrival order, same as the hand-rolled panel
            // this replaces relied on.
            const id = [...order].reverse().find(
              (i) => toolCalls.get(i)?.toolName === event.name && toolCalls.get(i)?.result === undefined,
            )
            if (id) {
              const call = toolCalls.get(id)!
              toolCalls.set(id, { ...call, result: event.summary, isError: event.ok === false })
            }
            yield { content: buildContent() }
          } else if (event.type === "proposalevent") {
            const id = `propose-${toolSeq++}`
            order.push(id)
            toolCalls.set(id, {
              type: "tool-call",
              toolCallId: id,
              toolName: event.proposal.action?.startsWith("propose_")
                ? event.proposal.action
                : `propose_${event.proposal.action}`,
              args: {},
              argsText: "",
              result: event.proposal,
            })
            yield { content: buildContent() }
          } else if (event.type === "error") {
            text += `\n\n**Error:** ${event.message}`
            yield { content: buildContent() }
          } else if (event.type === "done" && event.reason === "budget") {
            text += "\n\n*(stopped — turn budget reached)*"
            yield { content: buildContent() }
          }
        }
      }
    },
  }
}
