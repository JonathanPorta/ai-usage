#!/usr/bin/env bash
# release-cell.sh — record one build cell's outputs (no secret; RP-3).
#
#   release-cell.sh --cell ID --dir DIR
#
# `make release-build` writes the cell's files to DIR and declares each one in
# DIR/artifacts.json (blessed/release-cell/v1):
#
#   {"schema": "blessed/release-cell/v1",
#    "artifacts": [{"id", "filename", "platform", "install_kind", "sbom"?}]}
#
# The cell must be closed: DIR holds exactly artifacts.json, the declared
# files and their declared SBOMs — regular files, no subdirectory, no link.
# This writes DIR/cell.json {"schema": "blessed/release-cell-record/v1",
# "cell", "files": {name: sha256}} so the stage job can prove every byte it
# receives is what this cell built. Exit 0 · 1 refused · 2 usage.
# shellcheck disable=SC2016 # jq programs
set -euo pipefail
# This runs in every build cell, Windows and macOS included: native jq.exe
# writes CRLF, and a macOS runner may lack sha256sum.
jqr() { jq "$@" | tr -d '\r'; }
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
refuse() {
  echo "::error::release-cell: $1" >&2
  exit 1
}
cell="" dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cell) cell="${2:-}" ;;
    --dir) dir="${2:-}" ;;
    *)
      echo "release-cell: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift 2 || exit 2
done
[[ "$cell" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || {
  echo "usage: release-cell.sh --cell ID --dir DIR" >&2
  exit 2
}
[ -d "$dir" ] || refuse "make release-build wrote no $dir"
decl="$dir/artifacts.json"
[ -f "$decl" ] || refuse "make release-build declared nothing: no $decl"
jq -e '.schema == "blessed/release-cell/v1" and (.artifacts | type == "array" and length > 0)
  and all(.artifacts[]; (keys - ["id","filename","platform","install_kind","sbom"] | length == 0)
    and (.id | type == "string" and test("^[a-z0-9][a-z0-9._-]*$"))
    and (.filename | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._+-]*$"))
    and (.platform | type == "string" and test("^(any|[a-z0-9]+/[a-z0-9_]+)$"))
    and (.install_kind | IN("binary","tarball","appimage","dmg","msi","exe","rpm","deb","archive","image","other"))
    and ((.sbom // "x") | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._+-]*$")))' "$decl" >/dev/null 2>&1 ||
  refuse "$decl is not a valid blessed/release-cell/v1 declaration"
want="$( (
  echo artifacts.json
  jqr -r '.artifacts[] | .filename, (.sbom // empty)' "$decl"
) | sort)"
[ -z "$(uniq -d <<<"$want")" ] || refuse "$decl names a file twice: $(uniq -d <<<"$want" | tr '\n' ' ')"
[ -z "$(find "$dir" -mindepth 1 ! -type f -print -quit)" ] || refuse "$dir holds something other than regular files: $(find "$dir" -mindepth 1 ! -type f | head -3 | tr '\n' ' ')"
have="$(cd "$dir" && find . -type f ! -name cell.json | sed 's#^\./##' | sort)"
[ "$have" = "$want" ] || refuse "the cell is not closed. declared: $(tr '\n' ' ' <<<"$want") built: $(tr '\n' ' ' <<<"$have")"
while IFS= read -r f; do
  printf '%s\t%s\n' "$f" "$(sha256 "$dir/$f")"
done <<<"$want" | jq -R -s --arg c "$cell" '{schema: "blessed/release-cell-record/v1", cell: $c,
  files: (split("\n") | map(select(length > 0) | split("\t") | {(.[0]): .[1]}) | add)}' >"$dir/cell.json"
echo "release-cell: $cell — $(jqr '.artifacts | length' "$decl") artifact(s) declared and recorded"
