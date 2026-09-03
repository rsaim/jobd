/**
 * One message, expandable in place, used everywhere a message is read.
 *
 * The body is fetched on first expand and kept afterwards: bodies average
 * 3.5k characters and a page lists fifty rows, so shipping them inline is not
 * an option, and refetching on every toggle would be just as wrong in the
 * other direction (the query is `staleTime: Infinity` — an ingested body does
 * not change).
 *
 * The body pane is capped and scrolls inside itself. A recruiter thread with
 * its quoted history runs to 20,000 characters with no paragraph breaks; left
 * unbounded it pushes every control below it thousands of pixels down the
 * page and turns a triage pane into a wall.
 */

import { useState } from "react"
import { ArrowDownLeft, ArrowUpRight, ChevronRight } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import type { CommunicationRow } from "@/lib/api"
import { useMessage, useWrite } from "@/lib/api"
import { fmtIso, fmtStamp, linkify, words } from "@/lib/record"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { StageBadge, Tag, channelIcon } from "@/components/record-marks"

export function MessageBody({ id, dense = false }: { id: string; dense?: boolean }) {
  const { data, isPending, isError } = useMessage(id)
  const [whole, setWhole] = useState(false)
  const teach = useWrite<{ address: string; verdict: string }>(
    () => "/teach",
    ["messages", "triage", "home", "companies"],
    (v) => ({ address: v.address, verdict: v.verdict }),
  )

  if (isPending) {
    return (
      <div className="space-y-2 px-4 pb-5">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    )
  }
  if (isError || !data) {
    return <p className="px-4 pb-5 text-xs text-muted-foreground">Could not load this message.</p>
  }

  const teachable =
    data.correspondent &&
    data.correspondent.toLowerCase() !== (data.self_address ?? "").toLowerCase()

  return (
    <div className={cn("px-4 pb-5", dense && "px-0")}>
      <dl className="bg-muted/60 mb-3 grid gap-1 rounded-lg p-3 font-mono text-[11.5px] break-words text-muted-foreground">
        <div>
          from&nbsp;&nbsp;{data.sender_address || data.contact_address || "—"}
          {data.contact_name ? ` (${data.contact_name})` : ""}
        </div>
        {data.recipient_addresses.length > 0 && (
          <div>to&nbsp;&nbsp;&nbsp;&nbsp;{data.recipient_addresses.join(", ")}</div>
        )}
        <div>
          sent&nbsp;&nbsp;{fmtStamp(data.sent_at)} · {data.direction} · {data.channel}
        </div>
        {data.thread_id && <div>thread&nbsp;{data.thread_id}</div>}
        <div>
          how&nbsp;&nbsp;&nbsp;{data.tag}
          {data.classified_by ? ` (${data.classified_by})` : ""}
        </div>
      </dl>

      {data.evidences.length > 0 && (
        <p className="mb-3 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted-foreground">
          Cited as evidence for
          {data.evidences.map(([stage, role], index) => (
            <span key={`${stage}-${index}`} className="flex items-center gap-1">
              <StageBadge stage={stage} />
              {role && <span>{role}</span>}
            </span>
          ))}
        </p>
      )}

      {data.body_text ? (
        <>
          <div
            className={cn(
              "text-[13.5px] leading-relaxed break-words whitespace-pre-wrap",
              !whole && "max-h-[460px] overflow-y-auto pr-3",
            )}
          >
            {linkify(data.body_text).map((part, index) =>
              typeof part === "string" ? (
                <span key={index}>{part}</span>
              ) : (
                <a
                  key={index}
                  href={part.href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-primary underline underline-offset-2"
                >
                  {part.label}
                </a>
              ),
            )}
          </div>
          {data.body_text.length > 2000 && (
            <button
              type="button"
              onClick={() => setWhole((value) => !value)}
              className="mt-2 text-xs font-medium text-primary hover:underline"
            >
              {whole
                ? "Collapse"
                : `Show the whole message (${data.body_text.length.toLocaleString()} characters)`}
            </button>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          No text was extracted from this message. The raw bytes are still in object
          storage — <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">jobd rebuild</code>{" "}
          re-extracts them.
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {data.company_id && (
          <Button asChild size="sm" variant="outline">
            <Link to={`/company/${data.company_id}`}>{data.company_name}</Link>
          </Button>
        )}
        {data.contact_id && (
          <Button asChild size="sm" variant="outline">
            <Link to={`/contact/${data.contact_id}`}>
              {data.contact_name || data.contact_address}
            </Link>
          </Button>
        )}
        {/* Teaching a rule from what you are reading. Offered only when there
            is a correspondent that is not the reader: a rule about your own
            address would match your whole mailbox. */}
        {teachable && (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-muted-foreground">
              {data.correspondent}
            </span>
            <Button
              size="sm"
              variant="secondary"
              disabled={teach.isPending}
              onClick={() =>
                teach.mutate(
                  { address: data.correspondent!, verdict: "negative" },
                  { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                )
              }
            >
              Not job-related
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={teach.isPending}
              onClick={() =>
                teach.mutate(
                  { address: data.correspondent!, verdict: "undecided" },
                  { onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) },
                )
              }
            >
              Needs the model
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

export function MessageRow({
  message,
  defaultOpen = true,
  inCompany = false,
}: {
  message: CommunicationRow
  defaultOpen?: boolean
  /** Set on a company's own page, where the extracted company name is the
   *  same word on every row and printing it is noise rather than a reading. */
  inCompany?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div id={`msg-${message.id}`} className="border-b last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          "hover:bg-muted/60 grid w-full grid-cols-[auto_84px_46px_minmax(0,1fr)_auto] items-center gap-3 px-4 py-1.5 text-left text-[13px] transition-colors",
          // An open row is a heading for the body under it, so it gets the
          // signal bar on its edge — the same mark the active nav item uses,
          // meaning the same thing: this is where you are.
          open && "bg-muted/60 shadow-[inset_2px_0_0_0_var(--primary)]",
        )}
      >
        <ChevronRight
          className={cn(
            "text-muted-foreground size-3.5 transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="tabular text-muted-foreground text-xs">
          {fmtIso(message.sent_at)}
        </span>
        {/* Direction is the one thing every row is scanned for, so it is an
            arrow before it is a word: inbound points at you. */}
        <span
          className="text-muted-foreground flex items-center gap-1 font-mono text-[10px]"
          title={message.direction}
        >
          {message.direction === "inbound" ? (
            <ArrowDownLeft
              className="size-3 shrink-0"
              style={{ color: "var(--status-good)" }}
            />
          ) : (
            <ArrowUpRight
              className="size-3 shrink-0"
              style={{ color: "var(--status-warning)" }}
            />
          )}
          {message.direction === "inbound" ? "in" : "out"}
        </span>
        <span className="flex min-w-0 items-center gap-2">
          {/* Which pipe this arrived on — drawn only when it is not email.
              Today every row in this record is email, and an identical glyph
              on 58,331 rows is decoration, not information. The mark appears
              the day a LinkedIn InMail lands beside one, which is the only
              moment it says anything. */}
          {message.channel !== "email" &&
            (() => {
              const Channel = channelIcon(message.channel)
              return (
                <Channel
                  className="text-muted-foreground/70 size-3 shrink-0"
                  aria-label={message.channel}
                />
              )
            })()}
          <span className="truncate">{message.subject || "(no subject)"}</span>
          {/* What the pipeline read off this row, not merely that it read
              something. A bare "evidence" tag says a claim was made here and
              makes you open the message to find out which; naming the stage
              lets the list be checked by scanning it, which is the whole
              reason to show extractions on a list at all. Falls back to the
              old tag when the row is evidence for a stage the current
              derivation has since withdrawn. */}
          {message.stages.length > 0 ? (
            message.stages.map((stage) => <StageBadge key={stage} stage={stage} />)
          ) : message.is_stage_evidence ? (
            <Tag className="text-primary">evidence</Tag>
          ) : null}
          {message.link_role === "agency" && (
            <Badge variant="outline" className="shrink-0">
              agency
            </Badge>
          )}
        </span>
        <span className="hidden items-center gap-3 text-xs text-muted-foreground lg:flex">
          {/* The company this message was linked to. Absent on a company
              page, where every row has the same answer and the column would
              be one word repeated down the screen. */}
          {message.company_name && !inCompany && (
            <span className="max-w-40 truncate font-medium">
              {words(message.company_name)}
            </span>
          )}
          {message.role_title && <span className="truncate">{words(message.role_title)}</span>}
          <span className="max-w-56 truncate">
            {message.contact_name || message.contact_address || ""}
          </span>
          <Tag>{message.classification_tag}</Tag>
        </span>
      </button>
      {open && <MessageBody id={message.id} />}
    </div>
  )
}
