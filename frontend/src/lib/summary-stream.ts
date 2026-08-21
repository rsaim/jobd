/**
 * Streams a company summary from `/api/company/<id>/summary/stream` — the
 * same SSE frame shapes lib/chat-runtime.ts already parses for `/api/chat`
 * (`textdelta`/`error`/`done`), because `services/summaries.py` yields the
 * exact same `ChatEvent` union, just from a different endpoint.
 *
 * A plain async function, not a hook: it's called from two different
 * places (the company page's own panel, the chat panel's quick action)
 * that want different triggering (auto vs. click) and different rendering
 * homes, so the state belongs to each caller, not to this.
 */
export async function streamCompanySummary(
  companyId: string,
  opts: { model?: string; web?: boolean },
  onDelta: (chunkText: string) => void,
): Promise<{ text: string; done: "stop" | "budget" | "error" | "busy"; error?: string }> {
  const params = new URLSearchParams()
  if (opts.model) params.set("model", opts.model)
  if (opts.web) params.set("web", "1")

  const response = await fetch(`/api/company/${companyId}/summary/stream?${params}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
  })
  if (!response.ok || !response.body) {
    return { text: "", done: "error", error: `HTTP ${response.status}` }
  }

  let text = ""
  let done: "stop" | "budget" | "error" | "busy" = "stop"
  let error: string | undefined

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { done: readerDone, value } = await reader.read()
    if (readerDone) break
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
        onDelta(event.text)
      } else if (event.type === "error") {
        error = event.message
      } else if (event.type === "done") {
        done = event.reason
      }
    }
  }
  return { text, done, error }
}
