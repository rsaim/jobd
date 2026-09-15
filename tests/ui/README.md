# UI tests

Playwright layout, legibility, and navigation tests for the dashboard, run
against a throwaway demo stack — never against a developer's own database.

```bash
tests/ui/stack.sh up        # build + seed the synthetic corpus on :8111
cd tests/ui && npm ci && npx playwright install chromium
npm test                    # phone (iPhone SE) + tablet (iPad Mini) + desktop
npm run phone               # just the phone project
tests/ui/stack.sh down      # remove containers and volumes
```

What the suite asserts, on every route:

- **layout.spec.ts** — nothing hangs past the right edge, the document never
  scrolls sideways, and on phones: no nested vertical scroll traps, sticky
  chrome stays under a third of the viewport.
- **legibility.spec.ts** — truncated text still shows a readable fraction
  (this exists because a real bug rendered every message subject into a
  4px-wide cell — present in the DOM, invisible in fact), and on phones the
  standalone controls are at least 32px tall.
- **navigation.spec.ts** — the rail opens, navigates, and closes; every nav
  destination is reachable; the chat dock doesn't cover the page; message
  rows expand.

New page? Register it in `specs/routes.ts` and the whole contract applies
to it automatically.

The test stack is its own compose project (`jobd-uitest`, port 8111, tmpfs
database) so it can never touch the real stack on :8100. The database is
disposable by design — `stack.sh up` re-seeds it whenever it's empty.
