/**
 * What the model read, thread by thread.
 *
 * One row per paid model call — the thread's latest email (which quotes the
 * whole conversation) read once, classifying every member. Threads the free
 * tier resolved never appear here; this table IS the spend, and each row
 * shows what that one call bought: label, employer, the agency/client split
 * (an agency thread can pitch several employers), role, stage, relationship,
 * and how many messages the single reading covered.
 */

import { useState } from "react"
import { ListChecks } from "lucide-react"

import { useThreadExtractions, type ThreadExtraction } from "@/lib/api"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
} from "@/components/page"

const LABELS = ["positive", "negative", "unclassified"] as const

const LABEL_TONE: Record<string, string> = {
  positive: "text-emerald-600 dark:text-emerald-500",
  negative: "text-muted-foreground",
  unclassified: "text-amber-600 dark:text-amber-500",
}

function CompanyCell({ t }: { t: ThreadExtraction }) {
  const clients = t.client_companies ?? []
  return (
    <div className="min-w-0">
      <div className="truncate">
        {t.company_name ?? <span className="text-muted-foreground">—</span>}
        {t.company_kind === "agency" && (
          <span className="text-muted-foreground ml-1 text-[11px]">(agency)</span>
        )}
      </div>
      {t.agency_name && t.agency_name !== t.company_name && (
        <div className="text-muted-foreground truncate text-[11px]">
          via {t.agency_name}
        </div>
      )}
      {clients.length > 0 && (
        <div className="text-muted-foreground truncate text-[11px]">
          also: {clients.map((c) => c.name).join(", ")}
        </div>
      )}
    </div>
  )
}

export function ClassificationsPage() {
  const [label, setLabel] = useState<string | null>(null)
  const { data, isPending, error } = useThreadExtractions(label)
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const total = Object.values(data.counts).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-2">
      <PageHead
        icon={ListChecks}
        eyebrow="Machinery"
        title="Classifications"
        lede="Every thread the model actually read — one call on the latest email covers the whole conversation. Threads the free tier settled never appear here."
      />

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <button
          onClick={() => setLabel(null)}
          className={`rounded px-2 py-1 ${label === null ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted/50"}`}
        >
          All {total.toLocaleString()}
        </button>
        {LABELS.map((l) => (
          <button
            key={l}
            onClick={() => setLabel(l === label ? null : l)}
            className={`rounded px-2 py-1 tabular-nums ${label === l ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted/50"}`}
          >
            {l} {(data.counts[l] ?? 0).toLocaleString()}
          </button>
        ))}
      </div>

      {data.threads.length === 0 ? (
        <EmptyState icon={ListChecks} title="No model readings yet">
          Run a classify or review sweep — each thread the model reads lands
          here with what the call extracted.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Thread</TableHead>
                <TableHead>Label</TableHead>
                <TableHead>Company / agency</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>Relationship</TableHead>
                <TableHead className="text-right">Msgs</TableHead>
                <TableHead>Read</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.threads.map((t) => (
                <TableRow key={t.thread_id}>
                  <TableCell className="max-w-[26rem]">
                    <div className="truncate">{t.subject ?? t.thread_id}</div>
                    <div className="text-muted-foreground truncate text-[11px]">
                      {t.sender ?? ""}
                    </div>
                  </TableCell>
                  <TableCell className={LABEL_TONE[t.label] ?? ""}>
                    {t.label}
                  </TableCell>
                  <TableCell className="max-w-[18rem]">
                    <CompanyCell t={t} />
                  </TableCell>
                  <TableCell className="max-w-[12rem] truncate">
                    {t.role_title ?? ""}
                  </TableCell>
                  <TableCell>{t.stage ?? ""}</TableCell>
                  <TableCell>{t.relationship ?? ""}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {t.messages_covered}
                  </TableCell>
                  <TableCell className="text-muted-foreground whitespace-nowrap text-xs">
                    {t.updated_at
                      ? new Date(t.updated_at).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
