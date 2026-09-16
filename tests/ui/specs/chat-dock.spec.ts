import { expect, test } from "@playwright/test"

import { visit } from "./routes"

/**
 * The floating chat dock's own contract.
 *
 * This file exists because of two real bugs the route-wide sweeps could not
 * see: the expanded window sized itself against its launcher-width wrapper
 * (272px instead of 24rem), and the model picker — a full model id wide —
 * refused to shrink, pushing the minimize button past the window's
 * overflow-hidden edge. Both left every control present in the DOM, so only
 * geometry measured *inside the open dock* catches them. Runs on every
 * project (phone, tablet, desktop) like the rest of the suite.
 *
 * Needs a configured chat model (docker-compose.uitest.yml sets the real
 * stack's default, with no API key — no paid call can succeed). Opening the
 * dock on the home page fires no chat turn: auto-suggestions only happen on
 * company pages, from an already-generated summary.
 */

type Chrome = { chat_model: string | null; chat_models: string[] }

async function chatChrome(baseURL: string): Promise<Chrome> {
  const res = await fetch(`${baseURL}/api/chrome`)
  return (await res.json()) as Chrome
}

const dockWindow = "#chat-dock-window"

test.describe("chat dock", () => {
  let chrome: Chrome

  test.beforeEach(async ({ page, baseURL }) => {
    chrome = await chatChrome(baseURL!)
    test.skip(!chrome.chat_model, "chat is not configured in this environment")
    await visit(page, "/")
    await page.getByRole("button", { name: "Ask jobd" }).click()
    await expect(page.locator(dockWindow)).toBeVisible()
  })

  test("the window is dock-sized, not launcher-sized", async ({ page }) => {
    // The regression: w-[min(24rem,calc(100%-1rem))] resolved % against the
    // 288px launcher wrapper, silently shrinking the window to 272px.
    const dock = (await page.locator(dockWindow).boundingBox())!
    const viewport = page.viewportSize()!
    expect(dock.width).toBeGreaterThanOrEqual(Math.min(384, viewport.width - 32) - 1)
    expect(dock.x).toBeGreaterThanOrEqual(0)
    expect(dock.x + dock.width).toBeLessThanOrEqual(viewport.width + 1)
  })

  test("the model picker defaults to the server default and is not clipped", async ({
    page,
  }) => {
    test.skip(chrome.chat_models.length < 2, "single-model server renders no picker")
    const picker = page.locator(`${dockWindow} [role=combobox]`)
    await expect(picker).toBeVisible()

    // Defaults to the operator's own default model, shown by its short name.
    const short = chrome.chat_model!.split("/").pop()!
    await expect(picker).toContainText(short)

    // Fully inside the dock's box — overflow-hidden means anything past the
    // edge is invisible, not scrolled to.
    const dock = (await page.locator(dockWindow).boundingBox())!
    const box = (await picker.boundingBox())!
    expect(box.x, "picker starts inside the dock").toBeGreaterThanOrEqual(dock.x - 1)
    expect(
      box.x + box.width,
      "picker ends inside the dock",
    ).toBeLessThanOrEqual(dock.x + dock.width + 1)

    // And the name it shows is actually readable, not an ellipsis with a
    // rumour attached: the short label fits its box.
    const cut = await picker.evaluate((el) => {
      const value = el.querySelector("[data-slot=select-value]")
      return value ? value.scrollWidth - value.clientWidth : 0
    })
    expect(cut, "model name is visually truncated").toBeLessThanOrEqual(2)
  })

  test("minimize is visible inside the dock and actually minimizes", async ({
    page,
  }) => {
    const minimize = page.getByRole("button", { name: "Minimize chat" })
    await expect(minimize).toBeVisible()

    const dock = (await page.locator(dockWindow).boundingBox())!
    const box = (await minimize.boundingBox())!
    expect(
      box.x + box.width,
      "minimize sits inside the dock",
    ).toBeLessThanOrEqual(dock.x + dock.width + 1)

    await minimize.click()
    await expect(page.locator(dockWindow)).toBeHidden()
    // The launcher is back as the way to reopen.
    await expect(page.getByRole("button", { name: "Ask jobd" })).toBeVisible()
  })
})
