import { expect, test } from "@playwright/test"

import { ROUTES, companyId, resolve, visit } from "./routes"

/**
 * Text has to be readable, not merely present.
 *
 * This file exists because of a real bug: on a phone the message list rendered
 * every subject into a 4px-wide cell — about 2% of the string — so the page
 * passed every overflow check while being completely unusable. Nothing in a
 * DOM-presence assertion catches that; you have to measure how much of the
 * text actually fits.
 */

let company = ""

test.beforeAll(async ({ baseURL }) => {
  company = await companyId(baseURL!)
})

test.describe("truncation", () => {
  for (const route of ROUTES) {
    test(`truncated text still shows a usable fraction — ${route.path}`, async ({
      page,
    }) => {
      await visit(page, resolve(route.path, company))

      const crushed = await page.evaluate(() => {
        const out: { width: number; shown: number; text: string; cls: string }[] = []
        for (const el of Array.from(document.querySelectorAll("body *"))) {
          if (el.children.length > 0) continue
          const style = getComputedStyle(el)
          if (style.textOverflow !== "ellipsis") continue
          const text = (el.textContent ?? "").trim()
          if (text.length < 8) continue
          const box = el.getBoundingClientRect()
          if (box.width === 0 || box.height === 0) continue
          // Not truncated at all: nothing to judge.
          if (el.scrollWidth <= el.clientWidth + 2) continue
          const shown = box.width / el.scrollWidth
          // Under a fifth of the string visible, in under 60px, is not a
          // shortened label — it is an ellipsis with a rumour attached.
          if (shown < 0.2 && box.width < 60) {
            out.push({
              width: Math.round(box.width),
              shown: Math.round(shown * 100),
              text: text.slice(0, 50),
              cls: (el.getAttribute("class") ?? "").slice(0, 90),
            })
          }
        }
        return out
      })

      expect(
        crushed,
        `text crushed past legibility:\n${crushed
          .map((c) => `  ${c.width}px showing ${c.shown}% of "${c.text}" (.${c.cls})`)
          .join("\n")}`,
      ).toEqual([])
    })
  }
})

test.describe("touch targets", () => {
  test.skip(
    ({ viewport }) => !viewport || viewport.width > 500,
    "phone widths only — a tablet has room for compact controls",
  )

  // Not a blanket 44px rule: inline text links inside a sentence are
  // legitimately text-sized, and a dense record view would be unusable if every
  // row control were a 44px slab. This checks the standalone controls a thumb
  // is actually aimed at — buttons, tabs, selects and inputs.
  for (const route of ROUTES) {
    test(`controls are big enough to hit — ${route.path}`, async ({ page }) => {
      await visit(page, resolve(route.path, company))
      const small = await page.evaluate(() => {
        const out: { tag: string; h: number; label: string }[] = []
        const sel = "button, [role=button], [role=tab], select, input:not([type=hidden])"
        for (const el of Array.from(document.querySelectorAll(sel))) {
          const box = el.getBoundingClientRect()
          if (box.width === 0 || box.height === 0) continue
          // Controls nested in a table row or a list row are part of a dense
          // record surface, judged by the truncation rules instead.
          if (el.closest("tr, li")) continue
          if (box.height >= 32) continue
          // A checkbox or radio is legitimately small; what has to be
          // thumb-sized is the label wrapping it, which is the real hit area.
          const wrapping = el.closest("label")
          if (wrapping && wrapping.getBoundingClientRect().height >= 32) continue
          // Radix renders a checkbox as a <button> with a sibling <label
          // for=id>; the pair is the hit area, so measure the pair.
          const bound = el.id
            ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
            : null
          if (bound) {
            const row = el.parentElement?.getBoundingClientRect()
            if (row && row.height >= 32) continue
          }
          out.push({
            tag: el.tagName.toLowerCase(),
            h: Math.round(box.height),
            label: (el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 40),
          })
        }
        return out
      })
      const failures = small.map(
        (s) => `${route.name}: <${s.tag}> ${s.h}px tall — "${s.label}"`,
      )
      expect(failures, `controls under 32px tall:\n${failures.join("\n")}`).toEqual([])
    })
  }
})
