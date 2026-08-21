/**
 * ⌘K — jump anywhere.
 *
 * With 577 companies in the record, the companies page's filter box is the
 * wrong instrument for "open Stripe": it is a place you navigate to in order
 * to navigate again. The palette is the shortcut, and it searches the same
 * `/companies?search=` endpoint the page does, so there is no second index to
 * keep honest.
 *
 * Nothing here is a new capability — every entry is a route that already
 * exists and stays addressable (M7 gate 1). It is a faster way to reach them.
 */

import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  Award,
  Building2,
  Filter,
  LayoutGrid,
  Mail,
  MoonStar,
  Radar,
  ScrollText,
  Search,
  Sparkles,
  Sun,
  Telescope,
  Workflow,
  XCircle,
} from "lucide-react"

import { useCompanySearch } from "@/lib/api"
import { CompanyMark } from "@/components/company-mark"
import { Button } from "@/components/ui/button"
import { Kbd, KbdGroup } from "@/components/ui/kbd"
import { Spinner } from "@/components/ui/spinner"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"

const ROUTES = [
  { to: "/", label: "Home", hint: "What needs you today", icon: LayoutGrid },
  { to: "/companies", label: "Companies", hint: "Every company in the record", icon: Building2 },
  { to: "/messages", label: "Messages", hint: "The whole mailbox", icon: Mail },
  { to: "/offers", label: "Offers", hint: "Every offer, decided or not", icon: Award },
  { to: "/rejections", label: "Rejections", hint: "Every application the company said no to", icon: XCircle },
  { to: "/search", label: "Search", hint: "Ask the record by meaning", icon: Telescope },
  { to: "/scrape", label: "Scrape", hint: "Live seed-and-expand runs", icon: Radar },
  { to: "/triage", label: "Triage", hint: "Review queue, rules, audits", icon: Filter },
  { to: "/pipeline", label: "Pipeline", hint: "Sweep and ingest health", icon: Workflow },
  { to: "/prompts", label: "Prompts", hint: "Saved tones and instructions", icon: ScrollText },
]

export function CommandPalette({
  open,
  onOpenChange,
  onToggleTheme,
  dark,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onToggleTheme: () => void
  dark: boolean
}) {
  const navigate = useNavigate()
  const [query, setQuery] = useState("")
  const { data, isFetching } = useCompanySearch(query)

  useEffect(() => {
    if (!open) setQuery("")
  }, [open])

  const go = (to: string) => {
    onOpenChange(false)
    navigate(to)
  }

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Jump to"
      description="Search companies, or go to a page"
      className="rounded-xl"
    >
      <CommandInput
        placeholder="Search companies, or jump to a page…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>
          {query.trim().length < 2
            ? "Type at least two letters to search companies."
            : isFetching
              ? "Searching…"
              : "Nothing in the record matches that."}
        </CommandEmpty>

        {/* cmdk scores an item by its `value`, so every value below carries
            the words a reader would actually type. The server has already
            matched the companies; this second pass is only what hides the
            route list once you start typing a company name. */}
        <CommandGroup heading="Go to">
          {ROUTES.map((route) => (
            <CommandItem
              key={route.to}
              value={`${route.label} ${route.hint}`}
              onSelect={() => go(route.to)}
            >
              <route.icon />
              <span>{route.label}</span>
              <span className="text-muted-foreground ml-auto text-xs">{route.hint}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        {query.trim().length >= 3 && (
          <>
            <CommandSeparator />
            {/* The palette matches company names. What it cannot match is a
                sentence — "someone asking me for a referral" is nobody's
                name, and typing it here should not dead-end. This hands the
                same text to the search page, which does answer it. */}
            <CommandGroup heading="Ask the record">
              <CommandItem
                value={`meaning search ${query}`}
                onSelect={() => go(`/search?q=${encodeURIComponent(query.trim())}`)}
              >
                <Sparkles />
                <span className="truncate">Search for “{query.trim()}” by meaning</span>
              </CommandItem>
            </CommandGroup>
          </>
        )}

        {query.trim().length >= 2 && (
          <>
            <CommandSeparator />
            <CommandGroup
              heading={
                <span className="flex items-center gap-2">
                  Companies
                  {isFetching && <Spinner className="size-3" />}
                </span>
              }
            >
              {(data?.companies ?? []).slice(0, 8).map((company) => (
                <CommandItem
                  key={company.id}
                  value={`${company.canonical_name} ${company.domain ?? ""} ${company.id}`}
                  onSelect={() => go(`/company/${company.id}`)}
                >
                  {/* The company's own mark, not a generic building: a
                      palette is scanned, and a logo is recognised faster
                      than a name is read. */}
                  <CompanyMark id={company.id} name={company.canonical_name} />
                  <span className="truncate">{company.canonical_name}</span>
                  {company.domain && (
                    <span className="text-muted-foreground truncate font-mono text-[11px]">
                      {company.domain}
                    </span>
                  )}
                  <span className="tabular text-muted-foreground ml-auto text-[11px]">
                    {company.message_count} msg
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}

        <CommandSeparator />
        <CommandGroup heading="Display">
          <CommandItem
            value={`theme ${dark ? "light" : "dark"} appearance`}
            onSelect={() => {
              onToggleTheme()
              onOpenChange(false)
            }}
          >
            {dark ? <Sun /> : <MoonStar />}
            <span>Switch to {dark ? "light" : "dark"} theme</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}

/** The header's affordance for the palette. A visible target, because a
 *  shortcut nobody is told about is a shortcut nobody uses. */
export function CommandTrigger({ onClick }: { onClick: () => void }) {
  return (
    <Button
      variant="outline"
      onClick={onClick}
      className="text-muted-foreground h-8 w-full max-w-64 justify-start gap-2 px-2.5 font-normal"
    >
      <Search className="size-3.5" />
      <span className="text-[13px]">Search…</span>
      <KbdGroup className="ml-auto">
        <Kbd>⌘</Kbd>
        <Kbd>K</Kbd>
      </KbdGroup>
    </Button>
  )
}
