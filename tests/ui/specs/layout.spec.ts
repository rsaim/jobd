import { expect, test } from "@playwright/test"

import { ROUTES, companyId, overflowingElements, resolve, visit } from "./routes"

/**
 * The layout contract, asserted on every page at every viewport.
 *
 * These are the properties that make a page usable on a phone at all, as
 * opposed to merely rendering: nothing hangs off the side, the page scrolls in
 * one direction only, and the reader is never handed a scroll box inside a
 * scroll box.
 */

let company = ""

test.beforeAll(async ({ baseURL }) => {
  company = await companyId(baseURL!)
})

for (const route of ROUTES) {
  test.describe(route.name, () => {
    test(`renders without horizontal overflow — ${route.path}`, async ({ page }) => {
      await visit(page, resolve(route.path, company))

      // The page rendered something, not an error boundary.
      await expect(page.locator("body")).toContainText(route.ready)

      const offenders = await overflowingElements(page)
      expect(
        offenders,
        `elements hang past the right edge:\n${offenders
          .map((o) => `  <${o.tag} class="${o.cls}"> w=${o.width} right=${o.right} "${o.text}"`)
          .join("\n")}`,
      ).toEqual([])
    })

    test(`the document itself does not scroll sideways — ${route.path}`, async ({ page }) => {
      await visit(page, resolve(route.path, company))
      // Both readings must come from the same box. documentElement's
      // scrollWidth is measured against the layout viewport, so comparing it
      // to window.innerWidth (which counts the scrollbar gutter) reports a
      // ~30px phantom overflow on every page.
      const overflow = await page.evaluate(() => {
        const de = document.documentElement
        return {
          scrollWidth: de.scrollWidth,
          clientWidth: de.clientWidth,
          // What is actually reachable by scrolling right.
          overscroll: de.scrollWidth - de.clientWidth,
        }
      })
      // A couple of pixels of slack for sub-pixel rounding in the engine.
      expect(
        overflow.overscroll,
        `${route.name} scrolls ${overflow.overscroll}px sideways ` +
          `(content ${overflow.scrollWidth}px in a ${overflow.clientWidth}px viewport)`,
      ).toBeLessThanOrEqual(2)
    })
  })
}

test.describe("phone-only layout rules", () => {
  test.skip(
    ({ viewport }) => !viewport || viewport.width > 500,
    "phone widths only",
  )

  // A scrollable box inside a scrollable page is a touch trap: the gesture is
  // captured by whichever box is under the thumb. The message body pane is the
  // one deliberate exception (it caps a 20k-character thread).
  const ALLOWED = /max-h-\[460px\]|overflow-y-auto pr-3/
  for (const route of ROUTES) {
    test(`no nested vertical scroll trap — ${route.path}`, async ({ page }) => {
      await visit(page, resolve(route.path, company))
      const traps = await page.evaluate(() => {
        const out: { cls: string; box: number; content: number }[] = []
        for (const el of Array.from(document.querySelectorAll("main *"))) {
          const style = getComputedStyle(el)
          if (style.overflowY !== "auto" && style.overflowY !== "scroll") continue
          if (el.scrollHeight <= el.clientHeight + 4) continue
          const box = el.getBoundingClientRect()
          if (box.height < 80) continue
          out.push({
            cls: (el.getAttribute("class") ?? "").slice(0, 120),
            box: Math.round(box.height),
            content: el.scrollHeight,
          })
        }
        return out
      })
      const unexpected = traps.filter((t) => !ALLOWED.test(t.cls))
      expect(
        unexpected,
        `${route.name} has a nested scroller: ${JSON.stringify(unexpected)}`,
      ).toEqual([])
    })
  }

  for (const route of ROUTES) {
    test(`sticky chrome stays under a third of the viewport — ${route.path}`, async ({
      page,
    }) => {
      await visit(page, resolve(route.path, company))
      const worst = await page.evaluate(() => {
        let tallest = 0
        let cls = ""
        for (const el of Array.from(document.querySelectorAll("body *"))) {
          if (getComputedStyle(el).position !== "sticky") continue
          const box = el.getBoundingClientRect()
          if (box.height > tallest) {
            tallest = box.height
            cls = (el.getAttribute("class") ?? "").slice(0, 100)
          }
        }
        return { tallest: Math.round(tallest), cls, vh: window.innerHeight }
      })
      expect(
        worst.tallest,
        `${route.name}: sticky element ${worst.tallest}px of ${worst.vh}px — "${worst.cls}"`,
      ).toBeLessThanOrEqual(Math.round(worst.vh / 3))
    })
  }
})
