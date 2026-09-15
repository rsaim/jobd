import { defineConfig, devices } from "@playwright/test"

/**
 * UI tests run against the throwaway demo stack on :8111 (tests/ui/stack.sh),
 * never against a developer's own :8100 — that one holds a real mailbox, and
 * these tests click things.
 *
 * Three viewports, because "mobile friendly" is a claim about the small end
 * that must not be bought by breaking the large end: every layout assertion
 * runs on a phone, a tablet and a desktop, and the suite fails if a fix for
 * one breaks another.
 */
const BASE = process.env.JOBD_UI_BASE ?? "http://localhost:8111"

export default defineConfig({
  testDir: "./specs",
  // Layout assertions read geometry; a half-painted page measures wrong.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // Chromium everywhere on purpose: the device presets default to WebKit,
    // which makes a green run depend on a second browser download. These
    // tests assert layout geometry, not engine quirks, so one engine at the
    // right viewport is the honest cheap check — and `npx playwright install`
    // plus a `browserName` edit is all a WebKit pass would ever need.
    {
      name: "phone",
      use: { ...devices["iPhone SE"], browserName: "chromium" },
    },
    {
      name: "tablet",
      use: { ...devices["iPad Mini"], browserName: "chromium" },
    },
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
})
