/**
 * The client's entry point: providers, routes, shell.
 *
 * Routes mirror the addresses the server-rendered dashboard used, unchanged.
 * That is not nostalgia — every /company/<uuid> link in a chat proposal, a
 * bookmark or a shared URL has to keep resolving, and M7 gate 1 is exactly
 * the claim that they do.
 */

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter, Link, Outlet, Route, Routes } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import "./index.css"

import { Shell } from "@/components/shell"
import { SharePage } from "@/pages/share"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Button } from "@/components/ui/button"
import { PageHead } from "@/components/page"
import { HomePage } from "@/pages/home"
import { CompaniesPage } from "@/pages/companies"
import { CompanyPage } from "@/pages/company"
import { ContactPage } from "@/pages/contact"
import { MessagesPage } from "@/pages/messages"
import { OffersPage } from "@/pages/offers"
import { WaitingPage } from "@/pages/waiting"
import { ConversationsPage } from "@/pages/conversations"
import { RangeProvider } from "@/lib/range-context"
import { RejectionsPage } from "@/pages/rejections"
import { PipelinePage } from "@/pages/pipeline"
import { SearchPage } from "@/pages/search"
import { PromptsPage } from "@/pages/prompts"
import { ClassificationsPage } from "@/pages/classifications"
import { RunsPage } from "@/pages/runs"
import { ScrapePage } from "@/pages/scrape"
import { TriagePage } from "@/pages/triage"

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // An operational surface: a stale read is worse than a second request,
      // but refetching on every window focus turns a background tab into a
      // load generator against a local Postgres.
      refetchOnWindowFocus: false,
      staleTime: 15_000,
      retry: 1,
    },
  },
})

function NotFound() {
  return (
    <div>
      <PageHead
        eyebrow="Error 404"
        title="Nothing here"
        lede="That address doesn't point at anything in the record."
      />
      <div className="mt-6 flex gap-2">
        <Button asChild>
          <Link to="/">Back to Home</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/companies">Companies</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/messages">Messages</Link>
        </Button>
      </div>
    </div>
  )
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        {/* Inside the router: the range lives in the URL, so it needs
            useSearchParams, and every page below reads it from here. */}
        <RangeProvider>
        <TooltipProvider delayDuration={200}>
          <Routes>
            {/* Chrome-free: the share card is meant to be screenshotted,
                so it renders without the rail, dock, or any app furniture. */}
            <Route path="/share" element={<SharePage />} />
            <Route
              element={
                <Shell>
                  <Outlet />
                </Shell>
              }
            >
              <Route path="/" element={<HomePage />} />
              <Route path="/companies" element={<CompaniesPage />} />
              <Route path="/company/:companyId" element={<CompanyPage />} />
              <Route path="/contact/:contactId" element={<ContactPage />} />
              <Route path="/messages" element={<MessagesPage />} />
              <Route path="/search" element={<SearchPage />} />
              <Route path="/offers" element={<OffersPage />} />
              <Route path="/waiting" element={<WaitingPage />} />
              <Route path="/conversations" element={<ConversationsPage />} />
              <Route path="/rejections" element={<RejectionsPage />} />
              <Route path="/triage" element={<TriagePage />} />
              <Route path="/pipeline" element={<PipelinePage />} />
              <Route path="/prompts" element={<PromptsPage />} />
              <Route path="/scrape" element={<ScrapePage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/classifications" element={<ClassificationsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
          {/* bottom-right belongs to the chat dock now */}
          <Toaster position="bottom-center" />
        </TooltipProvider>
        </RangeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
