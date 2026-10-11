#!/usr/bin/env bash
# lifecycle-sut.sh — a Linux guest with a BOOTED init, for lifecycle tests that
# need daemon behaviour (systemd as PID 1, not merely `systemctl` on PATH).
# Shared by the producer gate (make release-lifecycle) and the surface's staged
# client rows; the gates stay separate, the guest is the same.
#
#   lifecycle-sut.sh start --name NAME --image IMAGE --platform PLATFORM
#                          [--install 'CMD'] [--init PATH] [-- DOCKER-RUN-ARGS...]
#   lifecycle-sut.sh stop  --name NAME
#
# `start` builds IMAGE + INSTALL with CMD [INIT] for PLATFORM, runs it detached
# (privileged, host cgroup namespace — what systemd in a container needs),
# polls until the manager leaves `initializing` (`running` and `degraded` both
# count: in a container some units legitimately cannot start), requires
# /run/systemd/system, and requires the guest's architecture to be PLATFORM's.
# A PLATFORM other than the runner's own runs under QEMU user emulation, which
# must already be registered (the workflow's setup-qemu step); the
# architecture check is what proves the guest really is that architecture.
# Without --init, the guest runs `sleep infinity` (no init: for products whose
# packages manage no service).
#
# Exit: 0 started/stopped · 1 the guest did not come up as required · 2 usage.
set -uo pipefail
die() {
  printf 'lifecycle-sut: %s\n' "$1" >&2
  exit 2
}
fail() {
  printf 'lifecycle-sut: %s\n' "$1" >&2
  [ -z "${name:-}" ] || docker logs "$name" 2>&1 | tail -20 >&2
  exit 1
}
cmd="${1:-}"
shift || true
name="" image="" platform="" install="" init=""
extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --name) name="${2:-}" && shift 2 ;;
    --image) image="${2:-}" && shift 2 ;;
    --platform) platform="${2:-}" && shift 2 ;;
    --install) install="${2:-}" && shift 2 ;;
    --init) init="${2:-}" && shift 2 ;;
    --)
      shift
      extra=("$@")
      break
      ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$name" ] || die "--name is required"
command -v docker >/dev/null 2>&1 || die "docker is required"
case "$cmd" in
  stop)
    docker rm -f "$name" >/dev/null 2>&1 || true
    exit 0
    ;;
  start) ;;
  *) die "usage: lifecycle-sut.sh start|stop --name NAME ..." ;;
esac
{ [ -n "$image" ] && [ -n "$platform" ]; } || die "start needs --image and --platform"
case "$platform" in
  linux/amd64) want="x86_64" ;;
  linux/arm64) want="aarch64" ;;
  linux/arm/v7) want="armv7l" ;;
  *) die "unsupported platform $platform" ;;
esac
tag="lifecycle-sut-$(printf '%s|%s|%s|%s' "$image" "$platform" "$install" "$init" | sha256sum | cut -c1-16)"
{
  printf 'FROM %s\n' "$image"
  [ -z "$install" ] || printf 'RUN %s\n' "$install"
  if [ -n "$init" ]; then
    printf 'STOPSIGNAL SIGRTMIN+3\nCMD ["%s"]\n' "$init"
  else
    printf 'CMD ["sleep", "infinity"]\n'
  fi
} | docker build -q --platform "$platform" -t "$tag" - >/dev/null || fail "cannot build the $platform guest from $image"
docker rm -f "$name" >/dev/null 2>&1 || true
if [ -n "$init" ]; then
  docker run -d --name "$name" --platform "$platform" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock ${extra[@]+"${extra[@]}"} "$tag" >/dev/null ||
    fail "cannot start the $platform guest"
  state=""
  for _ in $(seq 1 90); do
    state="$(docker exec "$name" systemctl is-system-running 2>/dev/null || true)"
    case "$state" in running | degraded) break ;; esac
    docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true || fail "the $platform guest exited while booting"
    sleep 2
  done
  case "$state" in running | degraded) ;; *) fail "init did not finish booting in the $platform guest (state: ${state:-none})" ;; esac
  docker exec "$name" test -d /run/systemd/system || fail "/run/systemd/system is absent: systemd is not the running init"
else
  docker run -d --name "$name" --platform "$platform" ${extra[@]+"${extra[@]}"} "$tag" >/dev/null || fail "cannot start the $platform guest"
fi
got="$(docker exec "$name" uname -m)"
[ "$got" = "$want" ] || fail "the guest is $got, not $want ($platform)"
host="$(uname -m)"
case "$host:$want" in x86_64:x86_64 | aarch64:aarch64 | arm64:aarch64) how="native" ;; *) how="emulated (QEMU user emulation on $host)" ;; esac
printf 'lifecycle-sut: %s up — %s on %s, %s%s\n' "$name" "$image" "$want" "$how" "${init:+, init $init booted}"
