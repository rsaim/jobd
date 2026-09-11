/**
 * The app shell: a collapsible rail, a sticky header, and the floating chat
 * dock.
 *
 * shadcn's sidebar anatomy, unmodified — including the icon-collapse mode,
 * which is what fixes the old rail's real bug: collapsing it used to hide the
 * navigation entirely, so the compact state cost you the thing the rail is
 * for. Icons stay, labels go, and every item keeps a tooltip.
 *
 * The rail is split into two groups because the record and the machine that
 * builds it are different jobs: Record is what the search is, Pipeline and
 * Triage are how it got there. Grouping them says so without a word of copy.
 *
 * Chat used to be a persistent 26rem third column; it is now a floating
 * bottom-right conversation window (chat-dock.tsx) in the LinkedIn/Facebook
 * messaging idiom — a docked launcher bar that expands upward, no overlay,
 * the page fully usable underneath. Every page stays addressable regardless
 * (M7 gate 1) — that property only ever depended on `url_context` following
 * the URL, which it still does.
 *
 * The nav badge and whether chat exists at all come from /api/chrome, which is
 * fetched once for the whole app rather than by every page.
 */

import { Link, useLocation } from "react-router-dom"
import {
  Award,
  Reply,
  Building2,
  Filter,
  Gauge,
  ListChecks,
  Mail,
  MoonStar,
  Phone,
  Radar,
  ScrollText,
  Send,
  Sun,
  Sunrise,
  Telescope,
  Workflow,
  XCircle,
} from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { useChrome } from "@/lib/api"
import { RangePicker } from "@/components/range-picker"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { ChatDock } from "@/components/chat-dock"
import { CompanyRailPanels } from "@/components/company-rail"
import { CommandPalette, CommandTrigger } from "@/components/command-palette"

const NAV = [
  {
    group: "Record",
    items: [
      // "Today", not "Home": the page is the day's briefing — what needs a
      // reply, what is about to go cold — and its name should say so. The
      // sunrise icon matches the page's own header.
      { to: "/", label: "Today", icon: Sunrise, match: (p: string) => p === "/" },
      // Directly under Today: this is the one list that is entirely work
      // owed, so it sits where you look after asking what today holds.
      {
        to: "/waiting",
        label: "Review",
        icon: Reply,
        match: (p: string) => p.startsWith("/waiting"),
      },
      // The one page that writes to the record rather than reading it:
      // calls and meetings the mailbox cannot show.
      {
        to: "/conversations",
        label: "Conversations",
        icon: Phone,
        match: (p: string) => p.startsWith("/conversations"),
      },
      {
        to: "/companies",
        label: "Companies",
        icon: Building2,
        match: (p: string) => p.startsWith("/companies") || p.startsWith("/company/"),
      },
      {
        to: "/messages",
        label: "Messages",
        icon: Mail,
        match: (p: string) => p.startsWith("/messages"),
      },
      {
        to: "/offers",
        label: "Offers",
        icon: Award,
        match: (p: string) => p.startsWith("/offers"),
      },
      {
        to: "/rejections",
        label: "Rejections",
        icon: XCircle,
        match: (p: string) => p.startsWith("/rejections"),
      },
      // Search is a way into the record, not a view of it, so it sits with
      // the record rather than with the machinery that builds it. The same
      // surface is a tab under Messages; this is the address you reach for
      // when the question isn't about a list you are already looking at.
      {
        to: "/search",
        label: "Search",
        icon: Telescope,
        match: (p: string) => p.startsWith("/search"),
      },
    ],
  },
  {
    group: "Machinery",
    items: [
      {
        to: "/scrape",
        label: "Sync",
        icon: Radar,
        match: (p: string) => p.startsWith("/scrape"),
      },
      {
        to: "/runs",
        label: "Runs",
        icon: Gauge,
        match: (p: string) => p.startsWith("/runs"),
      },
      {
        to: "/classifications",
        label: "Classifications",
        icon: ListChecks,
        match: (p: string) => p.startsWith("/classifications"),
      },
      {
        to: "/triage",
        label: "Triage",
        icon: Filter,
        match: (p: string) => p.startsWith("/triage"),
      },
      {
        to: "/pipeline",
        label: "Pipeline",
        icon: Workflow,
        match: (p: string) => p.startsWith("/pipeline"),
      },
      {
        to: "/prompts",
        label: "Prompts",
        icon: ScrollText,
        match: (p: string) => p.startsWith("/prompts"),
      },
    ],
  },
]

const FLAT = NAV.flatMap((section) => section.items)

function useTheme() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    try {
      localStorage.setItem("jobd-theme", dark ? "dark" : "light")
    } catch {
      /* private mode; the class is already applied */
    }
  }, [dark])
  return { dark, toggle: useCallback(() => setDark((value) => !value), []) }
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation()
  const { data: chrome, isPending: chromePending } = useChrome()
  const { dark, toggle } = useTheme()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const companyId = pathname.match(/^\/company\/([^/]+)/)?.[1]
  const current = FLAT.find((item) => item.match(pathname))

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <Link to="/">
                  {/* The mark is the only place the signal hue is used
                      decoratively — everywhere else it means "act here". */}
                  <div className="from-primary to-primary/70 text-primary-foreground ring-primary/25 flex aspect-square size-8 items-center justify-center rounded-lg bg-gradient-to-br shadow-sm ring-1">
                    <span className="font-mono text-sm font-semibold">j</span>
                  </div>
                  <div className="grid flex-1 text-left leading-tight">
                    <span className="display truncate text-[15px] font-semibold">jobd</span>
                    <span className="text-muted-foreground truncate font-mono text-[10px] tracking-[0.14em] uppercase">
                      the record
                    </span>
                  </div>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>

        <SidebarContent>
          {NAV.map((section) => (
            <SidebarGroup key={section.group} className="py-1">
              <SidebarGroupLabel className="font-mono text-[10px] tracking-[0.16em] uppercase">
                {section.group}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {section.items.map((item) => {
                    const active = item.match(pathname)
                    return (
                      <SidebarMenuItem key={item.to}>
                        <SidebarMenuButton
                          asChild
                          isActive={active}
                          tooltip={item.label}
                          className={
                            // The active item gets the signal hue and a bar
                            // on its leading edge — a tinted background alone
                            // is easy to miss at a glance across two groups.
                            // The bar is an inset shadow rather than a
                            // pseudo-element so the button's own rounding
                            // cannot clip it.
                            active
                              ? "data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary font-medium shadow-[inset_2px_0_0_0_var(--primary)]"
                              : undefined
                          }
                        >
                          <Link to={item.to}>
                            <item.icon />
                            <span>{item.label}</span>
                          </Link>
                        </SidebarMenuButton>
                        {item.to === "/triage" && chrome?.pending ? (
                          <SidebarMenuBadge className="tabular">
                            {chrome.pending}
                          </SidebarMenuBadge>
                        ) : null}
                      </SidebarMenuItem>
                    )
                  })}
                  {/* Reply drafting/sending is a chat capability now
                      (propose_draft_reply, chat-panel.tsx) — no standalone
                      list of every outbound_message row yet, so this stays a
                      pointer rather than a route promising a view that
                      doesn't exist. */}
                  {section.group === "Machinery" && (
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        disabled
                        tooltip="Ask jobd to draft a reply — a combined outbox view isn't built yet"
                        className="opacity-45"
                      >
                        <Send />
                        <span>Outbox</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}

          {/* Company-page context, appended to the same rail rather than
              replacing the nav above it — hidden in icon-collapsed mode
              (no room for a form at 48px) via the same group-data variant
              every other collapse-aware element in ui/sidebar.tsx uses. */}
          {companyId && (
            <div className="group-data-[collapsible=icon]:hidden">
              <CompanyRailPanels companyId={companyId} />
            </div>
          )}
        </SidebarContent>

        <SidebarFooter className="group-data-[collapsible=icon]:hidden">
          <p className="text-muted-foreground px-2 pb-1 font-mono text-[10px] leading-relaxed">
            local record · nothing leaves this machine
            {chrome?.chat_model ? " unless you ask chat" : ""}
          </p>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>

      <SidebarInset className="paper min-w-0">
        <header className="bg-background/70 supports-[backdrop-filter]:bg-background/55 sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur-md">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 !h-4" />
          <span className="flex items-center gap-2 text-sm font-medium">
            {current?.icon && <current.icon className="text-muted-foreground size-4" />}
            {current?.label ?? "jobd"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {/* Left of search, because it scopes what search runs against. */}
            <RangePicker />
            <div className="hidden sm:block">
              <CommandTrigger onClick={() => setPaletteOpen(true)} />
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={toggle}
              aria-label="Switch theme"
            >
              {dark ? <Sun /> : <MoonStar />}
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 px-4 pt-6 pb-24 md:px-8">{children}</main>
      </SidebarInset>

      {/* Chat is a floating dock (chat-dock.tsx), not a column: the fixed
          26rem aside couldn't share a phone with anything, and even on
          desktop it taxed every page for a panel used on some. Silent while
          chrome is still loading (avoids a one-frame flash of the launcher
          on every page load) — once it's loaded, the dock always renders:
          either the panel, or why the panel isn't there. */}
      {!chromePending && (
        <ChatDock
          model={chrome?.chat_model ?? null}
          models={chrome?.chat_models ?? []}
          companyId={companyId}
        />
      )}

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onToggleTheme={toggle}
        dark={dark}
      />
    </SidebarProvider>
  )
}
