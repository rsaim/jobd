/**
 * Every message, not just the 2% that resolved to a company.
 *
 * Mail the deterministic layer resolved to not-job-related is hidden by
 * default — that is, definitionally, mail nobody wants staring back at them
 * from an inbox view. "Show filtered-out mail" is still the only route into
 * the tens of thousands the prefilter rejected, which is the only place a
 * false negative can be found: an explicit opt-in, never the default.
 */

import { useSearchParams } from "react-router-dom"
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Mail,
  Search,
  Sparkles,
  X,
} from "lucide-react"

import { useMessages, qs } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { MessageRow } from "@/components/message-row"
import { SemanticSearch } from "@/components/semantic-search"
import { EmptyState, ErrorState, Loading, PageHead } from "@/components/page"

const ANY = "any"

export function MessagesPage() {
  const [params, setParams] = useSearchParams()
  const page = Number(params.get("page") ?? 1)
  const query = qs({
    search: params.get("search") ?? "",
    tag: params.get("tag") ?? "",
    recorded: params.get("recorded") ?? "",
    direction: params.get("direction") ?? "",
    channel: params.get("channel") ?? "",
    company: params.get("company") ?? "",
    on: params.get("on") ?? "",
    show_negative: params.get("show_negative") ?? "",
    order: params.get("order") ?? "desc",
    page,
  })
  const { data, isPending, error } = useMessages(query)

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value && value !== ANY) next.set(key, value)
    else next.delete(key)
    // Any filter change invalidates the offset: staying on page 7 of a
    // different result set is how a reader ends up staring at nothing.
    if (key !== "page") next.delete("page")
    setParams(next, { replace: true })
  }

  const day = params.get("on")
  const tab = params.get("tab") === "meaning" ? "meaning" : "text"

  return (
    <div className="space-y-2">
      <PageHead
        icon={Mail}
        eyebrow="The whole mailbox"
        title="Messages"
        lede="The whole mailbox, not just the part that resolved to a company. Mail the deterministic layer marked not-job-related is hidden by default — switch it on to audit for a false negative."
      />

      {/* Two ways into the same mailbox, not two pages: one matches the words
          you type, the other matches what you mean. Which one you want is a
          property of the question, not of where you navigated. Switching tabs
          drops the other tab's parameters rather than carrying them — a
          `page=7` from a keyword result set means nothing to a ranked list of
          twelve. */}
      <Tabs
        value={tab}
        onValueChange={(value) =>
          setParams(value === "meaning" ? { tab: "meaning" } : {}, { replace: true })
        }
        className="mt-5"
      >
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="text" className="gap-2">
            <Search className="size-3.5" />
            Text
          </TabsTrigger>
          <TabsTrigger value="meaning" className="gap-2">
            <Sparkles className="size-3.5" />
            Meaning
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === "meaning" ? (
        <SemanticSearch />
      ) : (
      <>
      <Card className="panel bg-card/85 sticky top-14 z-[5] mt-4 rounded-xl py-3 backdrop-blur-md">
        <CardContent className="flex flex-wrap items-center gap-2 px-3">
          <InputGroup className="h-9 min-w-64 flex-1">
            <InputGroupAddon>
              <Search className="size-3.5" />
            </InputGroupAddon>
            <InputGroupInput
              defaultValue={params.get("search") ?? ""}
              placeholder="Full-text search subject and body"
              onKeyDown={(e) => e.key === "Enter" && set("search", e.currentTarget.value)}
              onBlur={(e) => set("search", e.currentTarget.value)}
            />
          </InputGroup>
          <Select
            value={params.get("recorded") || ANY}
            onValueChange={(v) => set("recorded", v)}
          >
            <SelectTrigger className="w-44" aria-label="Recorded">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Recorded or not</SelectItem>
              <SelectItem value="1">On the record</SelectItem>
              <SelectItem value="0">Not resolved</SelectItem>
            </SelectContent>
          </Select>
          <Select value={params.get("tag") || ANY} onValueChange={(v) => set("tag", v)}>
            <SelectTrigger className="w-40" aria-label="Classifier">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any classifier</SelectItem>
              <SelectItem value="model">Model</SelectItem>
              <SelectItem value="rule">Rule</SelectItem>
              <SelectItem value="prefilter">Prefilter</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={params.get("direction") || ANY}
            onValueChange={(v) => set("direction", v)}
          >
            <SelectTrigger className="w-40" aria-label="Direction">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any direction</SelectItem>
              <SelectItem value="inbound">Inbound</SelectItem>
              <SelectItem value="outbound">Outbound</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={params.get("order") ?? "desc"}
            onValueChange={(v) => set("order", v)}
          >
            <SelectTrigger className="w-40" aria-label="Order">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="desc">Newest first</SelectItem>
              <SelectItem value="asc">Oldest first</SelectItem>
            </SelectContent>
          </Select>
          <div className="flex items-center gap-2">
            <Checkbox
              id="show-negative"
              checked={params.get("show_negative") === "1"}
              onCheckedChange={(checked) => set("show_negative", checked ? "1" : "")}
            />
            <Label htmlFor="show-negative" className="text-muted-foreground text-xs">
              Show filtered-out mail
            </Label>
          </div>
          {day && (
            <Button variant="secondary" size="sm" onClick={() => set("on", "")}>
              <CalendarDays />
              <span className="tabular">{day}</span>
              <X />
            </Button>
          )}
        </CardContent>
      </Card>

      {isPending ? (
        <Loading />
      ) : error ? (
        <ErrorState error={error} />
      ) : (
        <>
          <p className="text-muted-foreground mt-4 mb-2 font-mono text-[11px] tracking-[0.1em] uppercase">
            <span className="tabular text-foreground font-semibold">
              {data.total.toLocaleString()}
            </span>{" "}
            messages
          </p>
          {data.messages.length ? (
            <Card className="panel enter overflow-hidden rounded-xl py-0">
              {data.messages.map((message) => (
                <MessageRow key={message.id} message={message} defaultOpen={false} />
              ))}
            </Card>
          ) : (
            <EmptyState title="No message matches these filters" icon={Search}>
              Clear a filter, or switch on filtered-out mail.
            </EmptyState>
          )}

          {data.pages > 1 && (
            <div className="mt-4 flex items-center gap-3">
              <Button
                variant="outline"
                size="sm"
                disabled={data.page <= 1}
                onClick={() => set("page", String(data.page - 1))}
              >
                <ChevronLeft /> Newer
              </Button>
              <span className="tabular text-muted-foreground text-xs">
                Page {data.page} of {data.pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={data.page >= data.pages}
                onClick={() => set("page", String(data.page + 1))}
              >
                Older <ChevronRight />
              </Button>
            </div>
          )}
        </>
      )}
      </>
      )}
    </div>
  )
}
