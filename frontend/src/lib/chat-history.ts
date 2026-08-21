/**
 * Per-company chat history — localStorage, not the server. `services/chat.py`
 * stays exactly what its own docstring already says: "the conversation
 * lives in the page and nowhere else, never a server-side store" — the
 * server is still handed a full transcript with every turn and keeps none
 * of it (chat-runtime.ts). This only makes the *browser* remember one
 * transcript per company for longer than a single page load, so opening a
 * company's chat again later shows the same conversation instead of a
 * blank thread — with an explicit Reset to actually throw it away
 * (chat-panel.tsx's Reset button).
 */

import type { ExportedMessageRepositoryItem, ThreadHistoryAdapter } from "@assistant-ui/react"

const PREFIX = "jobd-chat:"

function historyKey(companyId: string): string {
  return `${PREFIX}${companyId}`
}

function readAll(companyId: string): ExportedMessageRepositoryItem[] {
  try {
    const raw = localStorage.getItem(historyKey(companyId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as ExportedMessageRepositoryItem[]
    // `Date` doesn't survive JSON — every message needs a real one back or
    // assistant-ui's own timestamp rendering breaks on the restored history.
    return parsed.map((item) => ({
      ...item,
      message: { ...item.message, createdAt: new Date(item.message.createdAt) },
    }))
  } catch {
    return [] // corrupt entry (or private-mode storage) — start fresh, don't crash the panel
  }
}

function writeAll(companyId: string, items: ExportedMessageRepositoryItem[]): void {
  try {
    localStorage.setItem(historyKey(companyId), JSON.stringify(items))
  } catch {
    /* quota exceeded / private mode — this turn just doesn't persist */
  }
}

/** Whether this company already has a saved conversation — checked
 *  synchronously (no adapter round trip) so chat-panel.tsx's auto-suggest
 *  effect can skip firing a fresh "next step" turn on top of one. */
export function hasSavedHistory(companyId: string): boolean {
  return readAll(companyId).length > 0
}

export function clearHistory(companyId: string): void {
  try {
    localStorage.removeItem(historyKey(companyId))
  } catch {
    /* nothing to clear */
  }
}

/** One `ThreadHistoryAdapter` per company — passed to `useLocalRuntime`'s
 *  `adapters.history`. Append-only (no `update`/`delete`): a proposal's
 *  Apply/Send state lives in its own component state
 *  (chat-panel.tsx's ProposalToolFallback), not in the persisted message,
 *  so a restored conversation shows the original exchange with fresh
 *  Apply buttons rather than mid-edit state that never happened. */
export function createCompanyHistoryAdapter(companyId: string): ThreadHistoryAdapter {
  return {
    async load() {
      return { messages: readAll(companyId) }
    },
    async append(item) {
      writeAll(companyId, [...readAll(companyId), item])
    },
  }
}
