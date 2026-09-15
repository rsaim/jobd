#!/usr/bin/env bash
# Bring the UI tests' throwaway demo stack up or down.
#
#   tests/ui/stack.sh up     build, migrate, seed the synthetic corpus, wait
#   tests/ui/stack.sh down   remove containers and volumes
#
# This never touches the root compose project. See docker-compose.uitest.yml
# for why that separation matters.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose=(docker compose -f "$here/docker-compose.uitest.yml")
port=8111

case "${1:-up}" in
up)
    # npx in frontend/ rewrites this lockfile and then `npm ci` fails the image
    # build at layer 3. Same guard the root justfile carries, same reason.
    if ! git -C "$here/../.." diff --quiet -- frontend/package-lock.json 2>/dev/null; then
        echo "restoring frontend/package-lock.json (local npx rewrote it)"
        git -C "$here/../.." checkout -- frontend/package-lock.json
    fi

    "${compose[@]}" up -d --build --wait

    # `jobd demo` refuses to run twice against the same database, so a stack
    # that is already seeded (a re-run without `down`) is fine as-is.
    "${compose[@]}" exec -T app jobd migrate up
    if ! "${compose[@]}" exec -T app jobd demo 2>&1 | tail -3; then
        echo "note: demo seed skipped (already seeded?) — continuing"
    fi

    for _ in $(seq 1 60); do
        if curl -fsS "http://localhost:$port/api/home" >/dev/null 2>&1; then
            echo "ui-test stack is up: http://localhost:$port"
            exit 0
        fi
        sleep 1
    done
    echo "error: no answer on :$port after 60s" >&2
    "${compose[@]}" logs --tail 40 app >&2
    exit 1
    ;;
down)
    "${compose[@]}" down -v
    ;;
*)
    echo "usage: stack.sh [up|down]" >&2
    exit 2
    ;;
esac
