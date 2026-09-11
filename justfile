# Task runner for jobd-ai. `just --list` shows everything.
#
# `just up` is the canonical way to start the dashboard: it builds and runs the
# compose stack (app + Postgres), which is what the app actually ships as. The
# bare-metal `serve-local` recipe below is the no-Docker path AGENTS.md
# documents, and needs a DATABASE_URL and ~/.openrouter_api_key of its own.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# --- Docker: the normal way to run this thing -------------------------------
#
# The image installs jobd into site-packages at BUILD time, so a plain
# `docker compose restart` keeps serving the code baked into the last image.
# Every recipe here that starts the app therefore passes --build: the cost of
# a no-op rebuild is seconds, the cost of forgetting is debugging a fix that
# was never deployed. OPENROUTER_API_KEY comes from .env via compose.

# Build and start the stack, then wait until the API actually answers.
up port="8100":
    #!/usr/bin/env bash
    set -euo pipefail
    # npx in frontend/ rewrites this lockfile (emnapi drift) and then `npm ci`
    # fails the image build with EUSAGE. Restore it rather than fail at layer 3.
    if ! git diff --quiet -- frontend/package-lock.json 2>/dev/null; then
        echo "restoring frontend/package-lock.json (local npx rewrote it)"
        git checkout -- frontend/package-lock.json
    fi
    docker compose up -d --build app
    for _ in $(seq 1 60); do
        if curl -fsS "http://localhost:{{ port }}/api/home" >/dev/null 2>&1; then
            echo "jobd is up: http://localhost:{{ port }}"
            exit 0
        fi
        sleep 1
    done
    echo "error: no answer on :{{ port }} after 60s — try: just logs" >&2
    exit 1

# Stop the stack, leaving the database volume intact.
down:
    docker compose down

# Rebuild and restart the app after a code change. Alias for `up`, which
# always rebuilds — named separately because "restart" is what you reach for.
restart port="8100": (up port)

# Follow the app container's logs.
logs *args:
    docker compose logs -f {{ args }}

# Apply pending database migrations inside the container.
migrate *args:
    docker compose exec app jobd migrate {{ args }}

# Run any jobd subcommand in the container: `just jobd derive-stages --help`.
jobd *args:
    docker compose exec app jobd {{ args }}

# A psql shell on the app's database.
psql:
    docker compose exec db psql -U jobd -d jobd

# Seed the synthetic mailbox — no credentials, no network.
demo:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose exec app jobd migrate up
    docker compose exec app jobd demo

# Run the test suite against the containerised database.
test *args:
    DATABASE_URL="$(just db-url)" .venv/bin/python -m pytest -q {{ args }}

# Print the host-side connection string, for scripts run outside the container.
# Postgres is published, so this reaches the same store the app calls db:5432.
db-url:
    @echo "postgresql://jobd:jobd@localhost:5432/jobd"

# Start the dashboard WITHOUT Docker. Needs a reachable Postgres in
# DATABASE_URL and a built frontend (`just frontend`). Prefer `just up`.
serve-local port="8100":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f ~/.openrouter_api_key ]; then
        echo "error: ~/.openrouter_api_key not found" >&2
        exit 1
    fi
    export OPENROUTER_API_KEY="$(tr -d '[:space:]' < ~/.openrouter_api_key)"
    export JOBD_JUDGE_MODEL="${JOBD_JUDGE_MODEL:-openrouter/google/gemini-3.7-flash}"
    export JOBD_CHAT_MODEL="${JOBD_CHAT_MODEL:-openrouter/google/gemini-3.7-flash}"
    export JOBD_BUCKET="${JOBD_BUCKET:-}"
    export JOBD_PRICE_PER_MTOK_IN="${JOBD_PRICE_PER_MTOK_IN:-0.375}"
    export JOBD_PRICE_PER_MTOK_OUT="${JOBD_PRICE_PER_MTOK_OUT:-1.875}"
    # LinkedIn companion push auth (extension/README.md). Absent file means
    # the endpoint answers 503 — feature off, not open.
    if [ -f ~/.jobd_linkedin_token ]; then
        export JOBD_LINKEDIN_TOKEN="$(tr -d '[:space:]' < ~/.jobd_linkedin_token)"
    fi
    exec .venv/bin/python -m uvicorn jobd.web.app:app --host 0.0.0.0 --port {{ port }}

# Generate the LinkedIn companion push token (idempotent; prints it once for the popup).
linkedin-token:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f ~/.jobd_linkedin_token ]; then
        umask 077
        openssl rand -hex 24 > ~/.jobd_linkedin_token
        echo "generated ~/.jobd_linkedin_token"
    fi
    echo "paste into the extension popup:"
    cat ~/.jobd_linkedin_token

# Ingest a jobd-linkedin.json produced by the console collector
# (extension/console-collector.js) — the no-install alternative to the
# companion extension. Posts to a running server; idempotent, so re-running an
# overlapping window is a server-side no-op.
linkedin-ingest file port="8100":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f "{{ file }}" ]; then
        echo "error: {{ file }} not found" >&2
        exit 1
    fi
    if [ ! -f ~/.jobd_linkedin_token ]; then
        echo "error: ~/.jobd_linkedin_token not found (run: just linkedin-token)" >&2
        exit 1
    fi
    token="$(tr -d '[:space:]' < ~/.jobd_linkedin_token)"
    curl -sS -X POST "http://localhost:{{ port }}/api/linkedin/push" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        --data-binary @"{{ file }}"
    echo

# Automated LinkedIn sync: pull new inbox messages server-side and ingest them.
# Needs the linkedin extra (`pip install -e '.[linkedin]'`) and a session cookie
# in ~/.jobd_linkedin_cookies (copy(document.cookie) from a logged-in
# linkedin.com tab). Idempotent; safe to cron:
#   */30 * * * * cd /workspaces/jobd-ai && just linkedin-sync >> ~/.jobd/linkedin.log 2>&1
linkedin-sync:
    #!/usr/bin/env bash
    set -euo pipefail
    export OPENROUTER_API_KEY="$(tr -d '[:space:]' < ~/.openrouter_api_key 2>/dev/null || true)"
    export JOBD_BUCKET="${JOBD_BUCKET:-}"
    exec .venv/bin/python -m jobd.services.linkedin_sync

# Extract the LinkedIn session cookie with Playwright into
# ~/.jobd_linkedin_cookies. Default opens a real browser you log into by hand,
# so run it on a machine WITH A SCREEN (a headless codespace has none — grab the
# cookie there and copy the file over). Needs `pip install -e '.[linkedin-login]'`
# and `playwright install chromium`. Headless fallback (no 2FA):
#   LINKEDIN_EMAIL=you@x LINKEDIN_PASSWORD=… just linkedin-cookies --headless
linkedin-cookies *args:
    .venv/bin/python tools/linkedin_login.py {{ args }}

# Stop whatever is listening on the server port.
kill port="8100":
    #!/usr/bin/env bash
    set -euo pipefail
    pids="$(lsof -ti "tcp:{{ port }}" 2>/dev/null || true)"
    if [ -z "$pids" ]; then
        echo "nothing listening on :{{ port }}"
        exit 0
    fi
    kill $pids
    echo "killed: $pids"

# Build the frontend into src/jobd/web/static (gitignored — rebuild per checkout).
frontend:
    cd frontend && npm run build

# The daily loop: scrape the last 3 days, then let the judge clear what the
# classifier punted on. Wire it to cron (or run by hand each morning):
#   0 7 * * * cd /workspaces/jobd-v2 && just daily >> ~/.jobd/daily.log 2>&1
daily:
    #!/usr/bin/env bash
    set -euo pipefail
    export OPENROUTER_API_KEY="$(tr -d '[:space:]' < ~/.openrouter_api_key)"
    export JOBD_JUDGE_MODEL="${JOBD_JUDGE_MODEL:-openrouter/google/gemini-3.7-flash}"
    export JOBD_DISTILL_MODEL="${JOBD_DISTILL_MODEL:-openrouter/z-ai/glm-5.2}"
    export JOBD_BUCKET="${JOBD_BUCKET:-}"
    .venv/bin/jobd scrape --window 3
    .venv/bin/jobd review sweep
    # Close the learning loop every day: whatever the model judged above is
    # compiled into deterministic rules (aggregates only, no content
    # re-read), so tomorrow's pass pays for less than today's did.
    .venv/bin/jobd distill --apply

# Compile the model's judged history into deterministic sender rules — the
# distillation half of online learning. GLM (the big head) judges the rule
# candidates; extraction stays on the cheap tier.
distill *args:
    #!/usr/bin/env bash
    set -euo pipefail
    export OPENROUTER_API_KEY="$(tr -d '[:space:]' < ~/.openrouter_api_key)"
    export JOBD_DISTILL_MODEL="${JOBD_DISTILL_MODEL:-openrouter/z-ai/glm-5.2}"
    .venv/bin/jobd distill {{ args }}

# Run the five-model panel discussion (writes to discussion_message).
discuss run_id="":
    #!/usr/bin/env bash
    set -euo pipefail
    export OPENROUTER_API_KEY="$(tr -d '[:space:]' < ~/.openrouter_api_key)"
    exec .venv/bin/python -m jobd.discuss.runner {{ run_id }}

# Serve the discussion transcript UI.
discuss-ui port="8200":
    JOBD_DISCUSS_PORT={{ port }} .venv/bin/python -m jobd.discuss.web
