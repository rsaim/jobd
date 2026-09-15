import { expect, test } from "@playwright/test"

import { ROUTES, companyId, resolve, visit } from "./routes"

/**
 * The navigation actually works by touch.
 *
 * Geometry tests prove a page is shaped correctly; these prove you can get to
 * it. On a phone the rail is a sheet that has to be opened, and a nav that
 * renders perfectly but cannot be opened is not mobile friendly.
 */

let company = ""

test.beforeAll(async ({ baseURL }) => {
  company = await companyId(baseURL!)
})

test("the rail opens, navigates, and closes again", async ({ page, isMobile }) => {
  await visit(page, "/")

  const trigger = page
    .locator("header")
    .getByRole("button", { name: /toggle sidebar/i })
  await expect(trigger).toBeVisible()
  await trigger.click()

  // On a phone the rail is a dialog (a sheet); on desktop it is always-present
  // chrome that this same click collapses instead.
  const companiesLink = page.getByRole("link", { name: "Companies", exact: true })
  await expect(companiesLink).toBeVisible()
  await companiesLink.click()

  await expect(page).toHaveURL(/\/companies$/)
  await expect(page.locator("body")).toContainText(/Companies/i)

  if (isMobile) {
    // The sheet must close on navigation — otherwise the destination is
    // rendered underneath a panel covering it.
    await expect(page.getByRole("dialog")).toBeHidden()
  }
})

test("every nav destination is reachable from the rail", async ({ page, isMobile }) => {
  // Guards against a route that exists but was never wired into the nav, and
  // against a phone sheet that silently drops items for lack of room.
  await visit(page, "/")
  if (isMobile) {
    await page.locator("header").getByRole("button", { name: /toggle sidebar/i }).click()
  }
  for (const route of ROUTES) {
    // The company detail page is reached from a list, not the rail.
    if (route.path.includes(":company")) continue
    if (route.name === "Search") continue // also a tab under Messages
    await expect(
      page.getByRole("link", { name: route.name, exact: true }),
      `"${route.name}" is missing from the rail`,
    ).toHaveCount(1)
  }
})

test("the chat dock does not cover the page it floats over", async ({ page }) => {
  // The dock is fixed to the bottom-right. On a phone its collapsed launcher
  // used to be 288px wide — most of the screen — sitting on top of the list.
  await visit(page, "/messages")
  const geom = await page.evaluate(() => {
    const dock = Array.from(document.querySelectorAll("div.fixed")).find((d) =>
      /Ask jobd/i.test(d.textContent ?? ""),
    )
    if (!dock) return null
    const box = dock.getBoundingClientRect()
    return {
      width: Math.round(box.width),
      viewport: window.innerWidth,
      share: box.width / window.innerWidth,
    }
  })
  if (!geom) test.skip(true, "chat dock is not configured in this environment")
  expect(
    geom!.share,
    `the dock launcher is ${geom!.width}px of ${geom!.viewport}px`,
  ).toBeLessThan(0.55)
})

test("a company page opens from the companies list", async ({ page }) => {
  await visit(page, "/companies")
  const firstCompany = page.locator("table a[href^='/company/']").first()
  await expect(firstCompany).toBeVisible()
  const name = (await firstCompany.textContent())?.trim() ?? ""
  await firstCompany.click()
  await expect(page).toHaveURL(/\/company\//)
  if (name) await expect(page.locator("body")).toContainText(name)
})

test("message rows expand in place", async ({ page }) => {
  await visit(page, resolve("/company/:company", company))
  const row = page.locator("button[aria-expanded]").first()
  if ((await row.count()) === 0) test.skip(true, "no messages on this company")
  const before = await row.getAttribute("aria-expanded")
  await row.click()
  await expect(row).toHaveAttribute("aria-expanded", before === "true" ? "false" : "true")
})
