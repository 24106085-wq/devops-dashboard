#!/usr/bin/env bash
# Smoke-test a built dashboard image: runs non-root, /health works, SQLite data
# survives a container restart AND container re-creation, and demo data is never
# shown unless DEMO_MODE is enabled.   Usage: scripts/docker-smoke.sh <image>
set -euo pipefail

IMAGE="${1:?usage: docker-smoke.sh <image>}"
NAME="dashboard-smoke-$$"
VOL="dashboard-smoke-data-$$"
PORT="${SMOKE_PORT:-5055}"
BASE="http://127.0.0.1:${PORT}"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start() {  # start [extra docker args...]
    docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:5000" -v "${VOL}:/data" \
        --security-opt no-new-privileges:true --cap-drop ALL \
        --cap-add CHOWN --cap-add SETUID --cap-add SETGID "$@" "$IMAGE" >/dev/null
}

wait_healthy() {
    for _ in $(seq 1 30); do
        status="$(docker inspect --format '{{.State.Health.Status}}' "$NAME" 2>/dev/null || true)"
        [ "$status" = "healthy" ] && return 0
        sleep 2
    done
    echo "Container never became healthy" >&2
    docker logs --tail 50 "$NAME" >&2 || true
    return 1
}

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "== simulate a volume created by an older root-run image"
docker volume create "$VOL" >/dev/null
docker run --rm -v "${VOL}:/data" --entrypoint sh "$IMAGE" -c 'touch /data/dashboard.db && chmod 600 /data/dashboard.db'

echo "== start with DEMO_MODE=true"
start -e DEMO_MODE=true
wait_healthy
[ "$(curl -fsS "${BASE}/health" | json 'd["status"]')" = "ok" ]
uid="$(docker exec "$NAME" sh -c "awk '/^Uid:/{print \$2}' /proc/1/status")"
[ "$uid" != "0" ] || { echo "App runs as root" >&2; exit 1; }
[ "$(curl -fsS "${BASE}/api/pipeline" | json 'd["source"]')" = "demo" ]

echo "== write a task, restart the container"
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"title":"smoke-task","owner":"ci"}' "${BASE}/api/tasks" >/dev/null
docker restart "$NAME" >/dev/null
wait_healthy
curl -fsS "${BASE}/api/tasks" | json '[t["title"] for t in d]' | grep -q smoke-task

echo "== re-create the container on the same volume without demo mode"
docker rm -f "$NAME" >/dev/null
start
wait_healthy
curl -fsS "${BASE}/api/tasks" | json '[t["title"] for t in d]' | grep -q smoke-task
src="$(curl -fsS "${BASE}/api/pipeline" | json 'd["source"]')"
[ "$src" = "unavailable" ] || { echo "Expected unavailable without GITHUB_REPO, got $src" >&2; exit 1; }

echo "Docker smoke test passed for $IMAGE"
