#!/bin/sh
# Smoke test for the Docker image: startup, health check, app user and volume permissions.
# Usage: scripts/docker-smoke-test.sh [image]   (default: laundry-booking:ci)
set -eu

IMAGE="${1:-laundry-booking:ci}"
BIND_DIR="$(mktemp -d)"
NAME=""

cleanup() {
    docker rm -f smoke-named smoke-bind smoke-user >/dev/null 2>&1 || true
    docker volume rm -f smoke-data >/dev/null 2>&1 || true
    # files in BIND_DIR may belong to other UIDs; remove them from inside a container
    docker run --rm --entrypoint sh -v "$BIND_DIR":/x "$IMAGE" -c 'rm -rf /x/*' >/dev/null 2>&1 || true
    rmdir "$BIND_DIR" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*"
    [ -n "$NAME" ] && docker logs "$NAME" 2>&1 | tail -30
    exit 1
}

wait_healthy() {  # port
    i=0
    while [ $i -lt 30 ]; do
        curl -fsS "http://127.0.0.1:$1/health" >/dev/null 2>&1 && return 0
        i=$((i + 1))
        sleep 1
    done
    return 1
}

as_root() {  # run a command in a throwaway container as root with BIND_DIR at /x
    docker run --rm --user 0:0 --entrypoint sh -v "$BIND_DIR":/x "$IMAGE" -c "$1"
}

echo "1) Named volume: starts, healthy, runs as uid 1000, CLI and notifier work"
NAME=smoke-named
docker run -d --name "$NAME" -p 5101:5000 -v smoke-data:/data \
    -e SECRET_KEY=ci -e ADMIN_USERNAME=admin -e ADMIN_PASSWORD=secret1234 "$IMAGE" >/dev/null
wait_healthy 5101 || fail "health endpoint did not answer"
uid="$(docker exec "$NAME" stat -c %u /proc/1)"
[ "$uid" = "1000" ] || fail "app runs as uid $uid, expected 1000"
printf 'secret1234\nsecret1234\n' | docker exec -i --user 1000:1000 "$NAME" \
    flask --app laundry create-user smoke --name Smoke >/dev/null || fail "create-user via docker exec"
docker exec --user 1000:1000 "$NAME" flask --app laundry send-notifications >/dev/null 2>&1 \
    || fail "send-notifications via docker exec"
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5101/login)"
[ "$code" = "200" ] || fail "/login returned $code"
docker rm -f "$NAME" >/dev/null

echo "2) Host folder owned by root, PUID/PGID set: ownership is fixed"
NAME=smoke-bind
as_root 'chown 0:0 /x && chmod 755 /x'
docker run -d --name "$NAME" -p 5102:5000 -v "$BIND_DIR":/data \
    -e PUID=4321 -e PGID=4321 -e SECRET_KEY=ci "$IMAGE" >/dev/null
wait_healthy 5102 || fail "health endpoint did not answer"
owner="$(as_root 'stat -c %u:%g /x/laundry.db')"
[ "$owner" = "4321:4321" ] || fail "laundry.db owned by $owner, expected 4321:4321"
uid="$(docker exec "$NAME" stat -c %u /proc/1)"
[ "$uid" = "4321" ] || fail "app runs as uid $uid, expected 4321"
docker rm -f "$NAME" >/dev/null

echo "3) --user without write access: fails with a clear message"
NAME=smoke-user
as_root 'chown -R 0:0 /x && chmod 755 /x'
docker run -d --name "$NAME" --user 4242:4242 -v "$BIND_DIR":/data -e SECRET_KEY=ci "$IMAGE" >/dev/null
timeout 60 docker wait "$NAME" >/dev/null || fail "container did not stop"
docker logs "$NAME" 2>&1 | grep -q 'is not writable for uid 4242' || fail "expected the 'not writable' message"

echo "OK: all Docker smoke tests passed"
