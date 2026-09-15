#!/usr/bin/env bash
# Reset the public demo: tear the stack down including volumes, bring it
# back, migrate, reseed. Runs nightly from cron (demo/cron) and by hand.
# Caddy's cert storage survives the `down -v` because its volumes are
# external (see docker-compose.demo.yml).
#
#   demo/demo-restart.sh            # reset in place
#   demo/demo-restart.sh --build    # also rebuild the app image (a deploy)
set -euo pipefail

cd "$(dirname "$0")/.."

# `down -v` wipes every volume — database, raw store, stored Gmail tokens.
# That is the point on the demo host and a disaster anywhere else, so this
# only runs on a machine demo/bootstrap.sh has stamped.
if [[ ! -f /etc/jobd-demo-host ]]; then
  echo "refusing to run: /etc/jobd-demo-host not found." >&2
  echo "This script destroys all jobd volumes and is meant only for the" >&2
  echo "demo VM, which demo/bootstrap.sh stamps with that marker." >&2
  exit 1
fi

compose() { docker compose -f docker-compose.yml -f demo/docker-compose.demo.yml "$@"; }

build=()
[[ "${1:-}" == "--build" ]] && build=(--build)

compose down -v --remove-orphans
compose up -d --wait ${build[@]+"${build[@]}"}
compose exec -T app jobd migrate up
compose exec -T app jobd demo
echo "demo reset complete: $(date -u '+%F %T') UTC"
