/**
 * Offline conversations — the calls that leave no mail.
 *
 * Every other page in this app reads a record derived from the mailbox. This
 * one writes to it, because some of a job search simply never lands in email:
 * a recruiter rings, a hiring manager talks through the team, an offer is
 * made on a call and turned down on the same call. Derivation cannot recover
 * any of that, so without somewhere to type it the record quietly under-counts
 * — a company whose whole process ran by phone looks like it never happened.
 *
 * A conversation may carry a stage, and that stage is a real `stage_event`
 * (`extracted_by='manual'`), so a verbal offer reaches the funnel, the offers
 * list and the timeline by the same road a derived one does. That is the
 * point of the feature, and also its danger: it is the only way a claim
 * enters the record without a message behind it. Hence the stage field is
 * optional and empty by default — most conversations are context, not a
 * verdict — and deleting a conversation takes its stage with it.
 */

import { useMemo, useState } from "react"
import { Phone, Plus, Trash2, Video, Users, MessageSquare } from "lucide-react"

import {
  useAddConversation,
  useCompanies,
  useConversations,
  useDeleteConversation,
  type ConversationRow,
} from "@/lib/api"
import { useRange } from "@/lib/range-context"
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const KIND_ICON: Record<string, typeof Phone> = {
  phone: Phone,
  video: Video,
  in_person: Users,
  other: MessageSquare,
}

const KIND_LABEL: Record<string, string> = {
  phone: "Call",
  video: "Video",
  in_person: "In person",
  other: "Other",
}

/** Today in the browser's own zone, as the date input wants it. */
function todayLocal(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function stageTone(stage: string): string {
  if (stage === "offer" || stage === "accepted")
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
  if (stage === "rejected" || stage === "declined" || stage === "withdrawn")
    return "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400"
  return "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400"
}

function ConversationEntry({
  row,
  onDelete,
  deleting,
}: {
  row: ConversationRow
  onDelete: (id: string) => void
  deleting: boolean
}) {
  const Icon = KIND_ICON[row.kind] ?? MessageSquare
  const when = new Date(row.occurred_at)
  return (
    <li className="flex gap-3 rounded-lg border bg-card/40 p-3">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{row.company_name}</span>
          <span className="text-xs text-muted-foreground">
            {when.toLocaleDateString(undefined, {
              year: "numeric",
              month: "short",
              day: "numeric",
            })}
          </span>
          <span className="text-xs text-muted-foreground">
            · {KIND_LABEL[row.kind] ?? row.kind}
          </span>
          {row.counterpart ? (
            <span className="text-xs text-muted-foreground">
              · with {row.counterpart}
            </span>
          ) : null}
          {row.stage ? (
            <Badge variant="outline" className={stageTone(row.stage)}>
              {row.stage.replace(/_/g, " ")}
            </Badge>
          ) : null}
          {row.stage && !row.application_id ? (
            /* A stage with nowhere to attach was not recorded as an event —
               say so, rather than let the badge imply the funnel moved. */
            <span className="text-xs text-amber-600 dark:text-amber-400">
              not in the funnel (no application)
            </span>
          ) : null}
        </div>
        {row.notes ? (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {row.notes}
          </p>
        ) : null}
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Delete conversation"
        disabled={deleting}
        onClick={() => onDelete(row.id)}
      >
        <Trash2 className="size-4" />
      </Button>
    </li>
  )
}

export function ConversationsPage() {
  const { query } = useRange()
  const { data, isPending, error } = useConversations(query)
  const add = useAddConversation()
  const remove = useDeleteConversation()

  const [companyQuery, setCompanyQuery] = useState("")
  const [companyId, setCompanyId] = useState("")
  const [occurredAt, setOccurredAt] = useState(todayLocal)
  const [kind, setKind] = useState("phone")
  const [counterpart, setCounterpart] = useState("")
  const [stage, setStage] = useState("none")
  const [notes, setNotes] = useState("")
  const [note, setNote] = useState<string | null>(null)

  // Only search once there is something to search on: the unfiltered company
  // list is long and this field is a picker, not a browser.
  const search = companyQuery.trim()
  const companies = useCompanies(search.length >= 2 ? `?q=${encodeURIComponent(search)}` : "")
  const matches = useMemo(
    () => (search.length >= 2 ? (companies.data?.companies ?? []).slice(0, 8) : []),
    [companies.data, search],
  )

  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { conversations, kinds, stages } = data
  const withStage = conversations.filter((c) => c.stage).length

  const submit = () => {
    if (!companyId) {
      setNote("Pick a company first.")
      return
    }
    add.mutate(
      {
        company_id: companyId,
        // Noon local, not midnight: a conversation placed at 00:00 lands on
        // the previous day in any zone west of UTC, which silently reorders
        // it against the mail it sits between.
        occurred_at: `${occurredAt}T12:00:00`,
        kind,
        counterpart: counterpart.trim() || null,
        notes: notes.trim(),
        stage: stage === "none" ? null : stage,
      },
      {
        onSuccess: (result) => {
          setNote(result.message ?? "Saved.")
          if (result.ok) {
            setNotes("")
            setCounterpart("")
            setStage("none")
            setCompanyId("")
            setCompanyQuery("")
          }
        },
        onError: () => setNote("Could not save that."),
      },
    )
  }

  return (
    <div className="space-y-4">
      <PageHead
        icon={Phone}
        eyebrow="The search, off the record"
        title="Conversations"
        lede={
          conversations.length
            ? `${conversations.length} recorded, ${withStage} carrying a stage the mail could not show.`
            : "Calls and meetings leave no mail. Record them here so the record is not just your inbox."
        }
      />

      <div className="space-y-3 rounded-lg border bg-card/40 p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="company">Company</Label>
            <Input
              id="company"
              placeholder="Start typing a name…"
              value={companyQuery}
              onChange={(e) => {
                setCompanyQuery(e.target.value)
                setCompanyId("")
              }}
            />
            {matches.length > 0 && !companyId ? (
              <ul className="max-h-40 overflow-y-auto rounded-md border bg-popover text-sm">
                {matches.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      className="w-full px-3 py-1.5 text-left hover:bg-accent"
                      onClick={() => {
                        setCompanyId(c.id)
                        setCompanyQuery(c.canonical_name)
                      }}
                    >
                      {c.canonical_name}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="when">When</Label>
            <Input
              id="when"
              type="date"
              value={occurredAt}
              onChange={(e) => setOccurredAt(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label>Kind</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {kinds.map((k) => (
                  <SelectItem key={k} value={k}>
                    {KIND_LABEL[k] ?? k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="who">Who (optional)</Label>
            <Input
              id="who"
              placeholder="Recruiter, hiring manager…"
              value={counterpart}
              onChange={(e) => setCounterpart(e.target.value)}
            />
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label>Stage this establishes (optional)</Label>
            <Select value={stage} onValueChange={setStage}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No stage — just a note</SelectItem>
                {stages.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              A stage here counts in the funnel and the timeline, exactly like
              one derived from mail. Leave it unset unless the conversation
              actually settled something.
            </p>
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="notes">Notes</Label>
            <Textarea
              id="notes"
              rows={3}
              placeholder="What was said, what was agreed…"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={submit} disabled={add.isPending}>
            <Plus className="size-4" />
            {add.isPending ? "Saving…" : "Record conversation"}
          </Button>
          {note ? <span className="text-sm text-muted-foreground">{note}</span> : null}
        </div>
      </div>

      {conversations.length === 0 ? (
        <EmptyState title="Nothing recorded yet" icon={Phone}>
          Phone screens, coffee chats and verbal offers leave no trace in a
          mailbox. Anything you add here joins the same record the rest of the
          app reads.
        </EmptyState>
      ) : (
        <ul className="space-y-2">
          {conversations.map((row) => (
            <ConversationEntry
              key={row.id}
              row={row}
              deleting={remove.isPending}
              onDelete={(id) => remove.mutate(id)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
