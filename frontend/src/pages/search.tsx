/**
 * Search, as its own address.
 *
 * The same surface lives as a tab under Messages, because "search the words"
 * and "search the meaning" are two ways into one mailbox and belong beside
 * each other. It is also a page in the rail, because reaching for search
 * through a list view is a detour: the question "what did anyone ever say
 * about relocation" is not a question about the messages page.
 *
 * One component serves both (`SemanticSearch`), so the two never drift — the
 * only thing this file adds is the heading and the address.
 */

import { Telescope } from "lucide-react"

import { SemanticSearch } from "@/components/semantic-search"
import { PageHead } from "@/components/page"

export function SearchPage() {
  return (
    <div className="space-y-2">
      <PageHead
        icon={Telescope}
        eyebrow="Ask the record"
        title="Search"
        lede="Describe what you're after in your own words and the record answers by meaning, not by keyword — results follow the box as you type. Nobody writes “compensation” in a subject line; they write “$150K–$300K + Equity”, and this finds it anyway."
      />
      <SemanticSearch />
    </div>
  )
}
