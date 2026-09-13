#!/usr/bin/env bash
# Codespace bootstrap: build the stack, migrate, and run the demo — which
# needs no credentials (classification replays the synthetic corpus's gold
# labels through the real pipeline). By the time the forwarded port opens,
# the dashboard is populated.
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose up -d --build
for _ in $(seq 1 60); do
  docker compose exec db pg_isready -U jobd >/dev/null 2>&1 && break
  sleep 2
done
docker compose exec app jobd migrate up
# Refuses a database that already holds the demo, so rebuilds don't re-seed.
docker compose exec -T app jobd demo || true

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "Demo loaded. Optional: set OPENROUTER_API_KEY (openrouter.ai/keys) to"
  echo "also enable the chat dock and company summaries, then: docker compose up -d"
fi
echo "jobd is up: open the forwarded port 8100."
