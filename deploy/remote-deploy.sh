#!/usr/bin/env bash
# Runs ON the deployment host (copied and invoked over SSH by the GitHub Actions deploy job).
# Usage: remote-deploy.sh <image-reference>
#
# - pulls the given image and restarts only the dashboard service
# - the named volume holding the SQLite database is NEVER removed (no "down -v", no "volume rm")
# - waits for the container health check; on failure rolls back to the previous image
set -euo pipefail

IMAGE="${1:?usage: remote-deploy.sh <image-reference>}"
DIR="${DEPLOY_DIR:-/opt/devops-dashboard}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-devops-dashboard}"
WAIT_SECONDS="${DEPLOY_WAIT_SECONDS:-90}"

cd "$DIR"
[ -f docker-compose.yml ] || { echo "ERROR: docker-compose.yml not found in $DIR" >&2; exit 2; }

container_id() { docker compose ps -q dashboard 2>/dev/null | head -n 1; }

wait_healthy() {
    local waited=0 cid status
    while [ "$waited" -lt "$WAIT_SECONDS" ]; do
        cid="$(container_id)"
        if [ -n "$cid" ]; then
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo unknown)"
            [ "$status" = "healthy" ] && return 0
        fi
        sleep 3
        waited=$((waited + 3))
    done
    return 1
}

start_with() {
    DASHBOARD_IMAGE="$1" docker compose up -d --no-build --remove-orphans dashboard
}

PREVIOUS=""
if [ -n "$(container_id)" ]; then
    PREVIOUS="$(docker inspect --format '{{.Config.Image}}' "$(container_id)" 2>/dev/null || true)"
fi

echo "Deploying $IMAGE (previous: ${PREVIOUS:-none})"
# Pull first: if the pull fails nothing has been touched and the running release keeps serving.
DASHBOARD_IMAGE="$IMAGE" docker compose pull dashboard
if start_with "$IMAGE" && wait_healthy; then
    docker image prune -f >/dev/null 2>&1 || true
    echo "Deployment healthy: $IMAGE"
    exit 0
fi

echo "ERROR: $IMAGE did not become healthy within ${WAIT_SECONDS}s" >&2
docker compose logs --tail 40 dashboard >&2 || true

if [ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "$IMAGE" ]; then
    echo "Rolling back to $PREVIOUS" >&2
    if start_with "$PREVIOUS" && wait_healthy; then
        echo "Rolled back to $PREVIOUS; the new release was NOT deployed." >&2
    else
        echo "ERROR: rollback to $PREVIOUS also failed; manual intervention required." >&2
    fi
else
    echo "No previous image to roll back to." >&2
fi
exit 1
