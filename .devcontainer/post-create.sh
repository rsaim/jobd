#!/usr/bin/env bash
# Codespace bootstrap: bring up the stack the README documents, then — if a
# key arrived via the Codespaces secret prompt — run the demo in the
# background so the dashboard is populated by the time anyone looks at it.
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose up -d --build
for _ in $(seq 1 60); do
  docker compose exec db pg_isready -U jobd >/dev/null 2>&1 && break
  sleep 2
done
docker compose exec app jobd migrate up

if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  # Idempotent by design upstream: jobd demo refuses a database that already
  # holds the demo account, so a container rebuild doesn't double-seed.
  echo "OPENROUTER_API_KEY found — seeding and classifying the demo mailbox in the background."
  echo "Watch it live on the Runs page; every dashboard page fills as it finishes."
  nohup docker compose exec -T \
      -e OPENROUTER_API_KEY -e JOBD_RUN_BUDGET=1 \
      app jobd demo > /tmp/jobd-demo.log 2>&1 &
else
  cat <<'MSG'
No OPENROUTER_API_KEY set. The dashboard is up (port 8100) but empty.
To load the demo (~100 flash-class calls, costs about a cent):

  1. Get a key at https://openrouter.ai/keys
  2. export OPENROUTER_API_KEY=sk-or-...
  3. docker compose exec -e OPENROUTER_API_KEY -e JOBD_RUN_BUDGET=1 app jobd demo

MSG
fi
echo "jobd is up: open the forwarded port 8100."
