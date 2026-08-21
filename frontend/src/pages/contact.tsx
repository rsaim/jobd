/**
 * One person, across every company they touched.
 *
 * The payoff for `contact_company` being a time-bounded table rather than a
 * column on `contact`: a recruiter who mailed you from three firms keeps all
 * three relationships instead of the newest overwriting the rest.
 */

import { Link, useParams } from "react-router-dom"
import { Building2, Mail, UserRound } from "lucide-react"

import { useContact } from "@/lib/api"
import { fmtMonth } from "@/lib/record"
import { Card } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { MessageRow } from "@/components/message-row"
import { KindBadge } from "@/components/record-marks"
import {
  EmptyState,
  ErrorState,
  Loading,
  PageHead,
  SectionHead,
} from "@/components/page"

export function ContactPage() {
  const { contactId = "" } = useParams()
  const { data, isPending, error } = useContact(contactId)
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  return (
    <div className="space-y-2">
      <PageHead
        icon={UserRound}
        eyebrow="Correspondent"
        title={data.display_name || data.identities[0]?.[1] || "Contact"}
        lede={
          <span className="font-mono text-xs">
            {data.identities.map(([channel, address]) => `${address} (${channel})`).join(" · ")}
          </span>
        }
      />

      <SectionHead icon={Building2} count={data.companies.length}>
        Companies
      </SectionHead>
      <Card className="panel overflow-hidden rounded-xl py-0">
        <Table>
          <TableHeader className="bg-muted/40">
            <TableRow>
              <TableHead>Company</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Role</TableHead>
              <TableHead className="text-right">Messages</TableHead>
              <TableHead className="text-right">Seen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.companies.map((row) => (
              <TableRow key={row.company_id}>
                <TableCell className="font-medium">
                  <Link to={`/company/${row.company_id}`} className="hover:underline">
                    {row.canonical_name}
                  </Link>
                </TableCell>
                <TableCell>
                  <KindBadge kind={row.kind} />
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {row.role_title || "—"}
                </TableCell>
                <TableCell className="tabular text-right">{row.message_count}</TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {fmtMonth(row.first_seen_at)} – {fmtMonth(row.last_seen_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <SectionHead icon={Mail} count={data.messages.length}>
        Messages
      </SectionHead>
      {data.messages.length ? (
        <Card className="panel enter overflow-hidden rounded-xl py-0">
          {data.messages.map((message) => (
            <MessageRow key={message.id} message={message} defaultOpen={false} />
          ))}
        </Card>
      ) : (
        <EmptyState title="No messages" icon={Mail} />
      )}
    </div>
  )
}
