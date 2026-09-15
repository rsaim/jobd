import type { Page } from "@playwright/test"

/**
 * Every addressable page in the app, plus the helpers the layout specs share.
 *
 * The list is the point: "test each tab aggressively" means the suite has to
 * fail when someone adds a route and forgets the phone, so ROUTES is the one
 * place a new page gets registered.
 */

export type Route = {
  /** URL path; `:company` is substituted with a real id from the API. */
  path: string
  /** What the nav calls it. */
  name: string
  /** A string that must appear once the page has actually rendered. */
  ready: RegExp
}

export const ROUTES: Route[] = [
  { path: "/", name: "Today", ready: /Today|Briefing|briefing/i },
  { path: "/waiting", name: "Review", ready: /Review|waiting|Your turn/i },
  { path: "/conversations", name: "Conversations", ready: /Conversations/i },
  { path: "/companies", name: "Companies", ready: /Companies/i },
  { path: "/company/:company", name: "Company", ready: /./ },
  { path: "/messages", name: "Messages", ready: /Messages/i },
  { path: "/offers", name: "Offers", ready: /Offers/i },
  { path: "/rejections", name: "Rejections", ready: /Rejections/i },
  { path: "/search", name: "Search", ready: /Search/i },
  { path: "/scrape", name: "Sync", ready: /Sync|sync/i },
  { path: "/runs", name: "Runs", ready: /Runs/i },
  { path: "/classifications", name: "Classifications", ready: /Classifications/i },
  { path: "/triage", name: "Triage", ready: /Triage/i },
  { path: "/pipeline", name: "Pipeline", ready: /Pipeline/i },
  { path: "/prompts", name: "Prompts", ready: /Prompts|tone/i },
]

/** A real company id from the demo corpus, so /company/:id is a real page. */
export async function companyId(baseURL: string): Promise<string> {
  const res = await fetch(`${baseURL}/api/companies?limit=1`)
  const body = (await res.json()) as { companies: { id: string }[] }
  if (!body.companies?.length) throw new Error("demo corpus has no companies")
  return body.companies[0].id
}

export function resolve(path: string, id: string): string {
  return path.replace(":company", id)
}

/**
 * Navigate and wait for the app to have painted real content.
 *
 * `networkidle` alone is not enough: this is a SPA whose panels stream in
 * after their queries settle, and a geometry assertion against a skeleton
 * measures the skeleton.
 */
export async function visit(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: "networkidle" })
  await page.locator("header").first().waitFor({ state: "visible" })
  // Skeletons carry animate-pulse; wait them out rather than sleeping blind.
  await page
    .locator(".animate-pulse")
    .first()
    .waitFor({ state: "detached", timeout: 10_000 })
    .catch(() => {
      /* no skeleton on this page, or it never cleared — the assertions say so */
    })
}

/**
 * The horizontal overflow check.
 *
 * Returns elements whose right edge is past the viewport and which are NOT
 * inside a deliberate horizontal scroller — a wide table in an
 * `overflow-x-auto` wrapper is a design decision, a card sticking off the
 * screen is a bug, and only the second should fail a test.
 */
export async function overflowingElements(page: Page) {
  return page.evaluate(() => {
    // The layout viewport, which is what elements are laid out against.
    // Not window.innerWidth: on a phone profile that includes the scrollbar
    // gutter, so it reads ~30px wider than the box elements actually get and
    // hides real overflow. Not scrollWidth either, which grows to fit the
    // overflow being looked for and would make the check vacuous.
    const width = document.documentElement.clientWidth
    type Hit = { tag: string; cls: string; width: number; right: number; text: string }
    const hits: Hit[] = []
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const box = el.getBoundingClientRect()
      if (box.width === 0 || box.height === 0) continue
      if (box.right <= width + 1) continue
      let scroller = false
      for (let p = el.parentElement; p; p = p.parentElement) {
        const overflowX = getComputedStyle(p).overflowX
        if (overflowX === "auto" || overflowX === "scroll") {
          scroller = true
          break
        }
      }
      if (scroller) continue
      // Fixed/sticky chrome is positioned against the visual viewport, which
      // in a headless phone profile is ~30px wider than the layout viewport
      // this check measures against. Skip such elements AND their subtrees —
      // a child of a fixed panel inherits the same frame of reference, and
      // judging it against clientWidth reports a phantom overflow.
      let anchored = false
      for (let p = el as Element | null; p; p = p.parentElement) {
        const position = getComputedStyle(p).position
        if (position === "fixed" || position === "sticky") {
          anchored = true
          break
        }
      }
      if (anchored) continue
      hits.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute("class") ?? "").slice(0, 120),
        width: Math.round(box.width),
        right: Math.round(box.right),
        text: (el.textContent ?? "").trim().slice(0, 60),
      })
    }
    // Report only outermost offenders: a wide parent lists every descendant.
    return hits.filter(
      (h, i) => !hits.some((o, j) => j !== i && o.right >= h.right && o.width > h.width),
    )
  })
}
