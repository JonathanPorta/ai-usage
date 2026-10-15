#!/usr/bin/env bash
# pkgrepo-client-rows.sh — install, upgrade and remove every product through a
# served generation with REAL native clients, on EVERY supported row
# (standards/releases/package-repositories.md PR-6 before activation, PR-15
# after). The surface activation gate — distinct from a producer's LP-6
# publication gate (release-lifecycle.sh), though both boot their guests with
# lifecycle-sut.sh.
#
#   pkgrepo-client-rows.sh --inventory INV --policy POLICY
#     [--serve-store DIR --tls-dir DIR] [--only-product NAME]
#
# A row is one inventory repository × one of its architectures × one of its
# approved targets. EVERY row runs, each target in its own guest (Debian 13 →
# debian:13, Fedora 44 → fedora:44); a target this runner has no guest for is
# refused before any guest starts, never assumed equivalent to another. A row
# for the runner's own architecture runs natively; any other (arm64/aarch64 on
# an x86_64 runner) runs that architecture under QEMU user emulation — which
# the workflow must register first (setup-qemu), costs runner time, and is
# reported as `emulated` on every line, never as native. A row that cannot run
# is a failure, never a skip: a skipped supported row would let a generation
# activate without its pre-activation install.
#
# For each package in a row: install the second-newest version (or the only
# one), run the producer's functional check (POLICY `smoke`), upgrade to the
# newest, check again, remove. Trust is bootstrapped from the PUBLISHED
# configuration and keys, each key checked against the inventory's
# fingerprint (pkgrepo-client-check.sh); every signature check stays on.
# Producers whose policy sets `systemd: true` get a guest with a booted
# systemd, as their scriptlets require.
#
# Staged (before activation): --serve-store DIR --tls-dir DIR serves a
# PUBLISHED store directory (the layout pkgrepo-publish.sh writes) through the
# real router (pkgrepo-router.js in pkgrepo-router-harness.mjs) behind an
# HTTPS front answering as the surface's own hostname on a private network,
# with a throwaway certificate only the client guests trust. Read-back (after
# activation): omit --serve-store; the clients use the real endpoint.
#
# Exit: 0 every row passed (and at least one ran) · 1 a row failed · 2 usage.
# shellcheck disable=SC2016  # jq programs: $-names are jq variables, not shell
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JQ="${JQ_BIN:-jq}"
die() {
  printf 'client-rows: %s\n' "$1" >&2
  exit 2
}
usage() {
  printf 'Usage: pkgrepo-client-rows.sh --inventory INV --policy POLICY [--serve-store DIR --tls-dir DIR] [--only-product NAME]\n' >&2
  exit 2
}
inv="" policy="" serve="" tls="" only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --inventory) inv="${2:-}" && shift 2 ;;
    --policy) policy="${2:-}" && shift 2 ;;
    --serve-store) serve="${2:-}" && shift 2 ;;
    --tls-dir) tls="${2:-}" && shift 2 ;;
    --only-product) only="${2:-}" && shift 2 ;;
    -h | --help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [ -z "$inv" ] || [ -z "$policy" ]; then usage; fi
if [ -n "$serve" ] && [ -z "$tls" ]; then die "--serve-store needs --tls-dir (tls-cert.pem, tls-key.pem)"; fi
if [ -n "$serve" ] && [ ! -f "$serve/o/_state/generation.json" ]; then die "--serve-store $serve has no activation pointer (o/_state/generation.json)"; fi
command -v docker >/dev/null 2>&1 || die "docker is required"
base="$("$JQ" -r .public_base_url "$inv")"
host="${base#https://}"
host="${host%%/*}"
case "$(uname -m)" in
  x86_64 | amd64) HOSTARCH=x86_64 ;;
  aarch64 | arm64) HOSTARCH=aarch64 ;;
  *) die "unsupported runner architecture $(uname -m)" ;;
esac
platform_of() { # deb or rpm architecture → docker platform
  case "$1" in
    amd64 | x86_64) echo linux/amd64 ;;
    arm64 | aarch64) echo linux/arm64 ;;
    *) return 1 ;;
  esac
}
level_of() { # platform → native | emulated (...)
  case "$1:$HOSTARCH" in
    linux/amd64:x86_64 | linux/arm64:aarch64) echo native ;;
    *) echo "emulated (QEMU user emulation on $HOSTARCH)" ;;
  esac
}
# Every row must be runnable BEFORE any guest starts: an architecture with no
# client platform is refused, not discovered halfway through.
target_of() { # format distro release → guest key (debian13|fedora44)
  case "$1:$2:$3" in
    apt:debian:13) echo debian13 ;;
    dnf:fedora:44) echo fedora44 ;;
    *) return 1 ;;
  esac
}
while IFS=$'\t' read -r rid arch; do
  platform_of "$arch" >/dev/null || die "$rid: architecture $arch has no client platform"
done < <("$JQ" -r '.repositories[] | .id as $i | (if .format == "apt" then .architectures[] else .arch end) | [$i, .] | @tsv' "$inv")
while IFS=$'\t' read -r rid fmt distro rel; do
  target_of "$fmt" "$distro" "$rel" >/dev/null || die "$rid: approved target $distro $rel has no client guest here — it cannot be gated, so it must not be approved"
done < <("$JQ" -r '.repositories[] | .id as $i | .format as $f | .targets[] | [$i, $f, .distro, .release] | @tsv' "$inv")
NET="pkgrepo-rows-$$"
IDS=()
cleanup() {
  [ "${#IDS[@]}" -eq 0 ] || docker rm -f "${IDS[@]}" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT
docker network create "$NET" >/dev/null || die "cannot create a docker network"

if [ -n "$serve" ]; then
  router="$(docker run -d --network "$NET" --network-alias router -v "$(cd "$serve" && pwd)":/store:ro -v "$HERE":/r:ro \
    -e ROUTER_BIND=0.0.0.0 -e ROUTER_PORT=8080 -e ROUTER=/r/pkgrepo-router.js \
    node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 \
    node /r/pkgrepo-router-harness.mjs /store /tmp/port)" || die "cannot start the router"
  IDS+=("$router")
  conf="$(mktemp)"
  printf 'server {\n  listen 443 ssl;\n  server_name %s;\n  ssl_certificate /tls/tls-cert.pem;\n  ssl_certificate_key /tls/tls-key.pem;\n  location / {\n    proxy_pass http://router:8080;\n    proxy_http_version 1.1;\n  }\n}\n' "$host" >"$conf"
  chmod 644 "$conf"
  web="$(docker run -d --network "$NET" --network-alias "$host" -v "$(cd "$tls" && pwd)":/tls:ro \
    -v "$conf":/etc/nginx/conf.d/default.conf:ro \
    nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10)" || die "cannot start the staging server"
  IDS+=("$web")
  up=0
  for _ in $(seq 1 20); do
    if docker exec "$web" nc -z 127.0.0.1 443 2>/dev/null; then
      up=1
      break
    fi
    sleep 1
  done
  [ "$up" = 1 ] || die "the staging server did not come up"
  up=0
  for _ in $(seq 1 20); do
    if docker exec "$router" test -s /tmp/port 2>/dev/null; then
      up=1
      break
    fi
    sleep 1
  done
  [ "$up" = 1 ] || die "the router did not come up: $(docker logs "$router" 2>&1 | tail -3)"
fi

# One guest per row and package: the distro release plus exactly what
# bootstrapping trust needs (curl, gpg, CA store), and systemd as init for
# producers whose packages manage a service.
guest_install() { # debian13|fedora44
  case "$1" in
    debian13) echo 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd curl gnupg ca-certificates >/dev/null && rm -rf /var/lib/apt/lists/*' ;;
    fedora44) echo 'dnf -y -q install systemd gnupg2 curl ca-certificates >/dev/null && dnf clean all' ;;
  esac
}
start_client() { # name distro platform systemd(true|false)
  local img init=()
  case "$2" in debian13) img=debian:13 ;; fedora44) img=fedora:44 ;; esac
  [ "$4" != true ] || case "$2" in debian13) init=(--init /lib/systemd/systemd) ;; fedora44) init=(--init /usr/lib/systemd/systemd) ;; esac
  bash "$HERE/lifecycle-sut.sh" start --name "$1" --image "$img" --platform "$3" --install "$(guest_install "$2")" ${init[@]+"${init[@]}"} \
    -- --network "$NET" >/dev/null || return 1
  if [ -n "$tls" ]; then
    docker cp "$tls/tls-cert.pem" "$1:/root/staging-ca.pem" >/dev/null
    docker exec "$1" sh -c 'if [ -d /usr/local/share/ca-certificates ]; then cp /root/staging-ca.pem /usr/local/share/ca-certificates/staging.crt && update-ca-certificates >/dev/null 2>&1; else cp /root/staging-ca.pem /etc/pki/ca-trust/source/anchors/staging.pem && update-ca-trust; fi' ||
      return 1
  fi
  docker cp "$HERE/pkgrepo-client-check.sh" "$1:/usr/local/bin/pkgrepo-client-check.sh" >/dev/null
}

pass=0 failed=0 native=0 emulated=0 n=0
repo_key="${base}$("$JQ" -r .repository_key.public_key_path "$inv")=$("$JQ" -r .repository_key.fingerprint "$inv")"
while IFS= read -r repo; do
  id="$("$JQ" -r .id <<<"$repo")"
  fmt="$("$JQ" -r .format <<<"$repo")"
  path="$("$JQ" -r .path <<<"$repo")"
  product="$("$JQ" -r .product <<<"$repo")"
  producer="$("$JQ" -r .producer_repo <<<"$repo")"
  [ -z "$only" ] || [ "$only" = "$product" ] || continue
  smoke="$("$JQ" -r --arg p "$producer" '.producers[$p].smoke // empty' "$policy")"
  sysd="$("$JQ" -r --arg p "$producer" '.producers[$p].systemd // false' "$policy")"
  [ -n "$smoke" ] || die "no functional check (smoke) for $producer in $policy"
  keys=(--key "$repo_key")
  targets="$("$JQ" -r '.targets[] | "\(.distro):\(.release)"' <<<"$repo")"
  if [ "$fmt" = apt ]; then
    cfg="${base}${path}${product}.sources"
    arches="$("$JQ" -r '.architectures[]' <<<"$repo")"
  else
    arches="$("$JQ" -r .arch <<<"$repo")"
    cfg="${base}${path%"$arches/"}${product}.repo"
    while IFS= read -r k; do keys+=(--key "$k"); done < <("$JQ" -r --arg p "$producer" --arg b "$base" '.producer_keys[] | select(.producer_repo == $p) | "\($b)\(.public_key_path)=\(.fingerprint)"' "$inv")
  fi
  for target in $targets; do
    distro="$(target_of "$fmt" "${target%%:*}" "${target#*:}")" || die "$id: approved target $target has no client guest"
    case "$distro" in debian13) label_distro="Debian 13" ;; fedora44) label_distro="Fedora 44" ;; esac
    for arch in $arches; do
      plat="$(platform_of "$arch")" || die "$id: architecture $arch has no client platform"
      lvl="$(level_of "$plat")"
      if [ "$fmt" = apt ]; then sel='.arch == $a or .arch == "all"'; else sel='true'; fi
      rowpk=0
      while IFS= read -r name; do
        vers=()
        while IFS= read -r v; do vers+=("$v"); done < <("$JQ" -r --arg r "$id" --arg n "$name" --arg a "$arch" \
          ".packages[] | select(.repository == \$r and .name == \$n and ($sel)) | \"\\(.version)-\\(.revision)\"" "$inv" | sort -uV)
        [ "${#vers[@]}" -gt 0 ] || continue
        rowpk=$((rowpk + 1))
        newest="${vers[${#vers[@]} - 1]}"
        first="$newest"
        [ "${#vers[@]}" -lt 2 ] || first="${vers[${#vers[@]} - 2]}"
        args=(--format "$fmt" --config-url "$cfg" "${keys[@]}" --install "$name=$first" --check "$smoke" --remove)
        [ "$first" = "$newest" ] || args+=(--upgrade-to "$newest")
        label="$id ($fmt, $arch, $label_distro, $lvl): $name $first$([ "$first" = "$newest" ] || echo " → $newest")"
        n=$((n + 1))
        cid="pkgrepo-row-$$-$n"
        IDS+=("$cid")
        if ! start_client "$cid" "$distro" "$plat" "$sysd"; then
          echo "  ✗ $label — the $plat guest could not start (is QEMU registered for $plat?)"
          failed=$((failed + 1))
          continue
        fi
        if out="$(docker exec "$cid" bash /usr/local/bin/pkgrepo-client-check.sh "${args[@]}" 2>&1)"; then
          echo "  ✓ $label: installed, checked, $([ "$first" = "$newest" ] || echo 'upgraded, ')removed"
          pass=$((pass + 1))
          case "$lvl" in native) native=$((native + 1)) ;; *) emulated=$((emulated + 1)) ;; esac
        else
          echo "  ✗ $label"
          printf '%s\n' "$out" | tail -6 | sed 's/^/      /'
          failed=$((failed + 1))
        fi
        docker rm -f "$cid" >/dev/null 2>&1
      done < <("$JQ" -r --arg r "$id" '[.packages[] | select(.repository == $r) | .name] | unique | .[]' "$inv")
      if [ "$rowpk" -eq 0 ]; then
        echo "  ✗ $id ($fmt, $arch, $label_distro): no package serves this row"
        failed=$((failed + 1))
      fi
    done
  done
done < <("$JQ" -c '.repositories[]' "$inv")
echo "client-rows: $pass passed ($native native, $emulated emulated under QEMU on this $HOSTARCH runner), $failed failed, 0 skipped"
[ "$failed" -eq 0 ] && [ "$pass" -gt 0 ]
