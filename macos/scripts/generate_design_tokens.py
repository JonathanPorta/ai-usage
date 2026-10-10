#!/usr/bin/env python3
"""Generate Swift design tokens from the canonical DTCG source.

Usage:
    generate_design_tokens.py [--source PATH] [--output PATH] [--check]

`--check` regenerates in memory and exits 1 when the committed output differs,
so `make design-build-check` can detect drift without touching the tree.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "design" / "tokens.json"
DEFAULT_OUTPUT = ROOT / "Sources" / "AIUsageDesign" / "DesignTokens.generated.swift"
APPEARANCE_EXTENSION = "codes.porta.appearance"
REFERENCE = re.compile(r"^\{([A-Za-z0-9_.]+)\}$")


def lookup(tokens: dict[str, Any], dotted: str) -> dict[str, Any]:
    node: Any = tokens
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unresolved token reference: {{{dotted}}}")
        node = node[part]
    if not isinstance(node, dict) or "$value" not in node:
        raise ValueError(f"reference does not name a token: {{{dotted}}}")
    return node


def resolve(tokens: dict[str, Any], token: dict[str, Any], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    value = token["$value"]
    if isinstance(value, str):
        match = REFERENCE.match(value)
        if match:
            target = match.group(1)
            if target in seen:
                raise ValueError(f"token reference cycle: {' -> '.join(seen + (target,))}")
            return resolve(tokens, lookup(tokens, target), seen + (target,))
    return token


def hex_literal(value: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError(f"expected #rrggbb color, got {value!r}")
    return "0x" + value[1:].upper()


def color_pair(tokens: dict[str, Any], token: dict[str, Any]) -> tuple[str, str]:
    resolved = resolve(tokens, token)
    light = resolved["$value"]
    dark = resolved.get("$extensions", {}).get(APPEARANCE_EXTENSION, {}).get("dark", light)
    return hex_literal(light), hex_literal(dark)


def dimension(token: dict[str, Any]) -> str:
    value = token["$value"]
    if not isinstance(value, dict) or value.get("unit") not in ("pt", "ms"):
        raise ValueError(f"expected pt/ms dimension, got {value!r}")
    number = value["value"]
    return f"{float(number):g}" if float(number) != int(number) else str(int(number))


def leaves(group: dict[str, Any], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for key in sorted(k for k in group if not k.startswith("$")):
        node = group[key]
        name = f"{prefix}{key[0].upper()}{key[1:]}" if prefix else key
        if isinstance(node, dict) and "$value" in node:
            out.append((name, node))
        elif isinstance(node, dict):
            out.extend(leaves(node, name))
    return out


def swift_name(name: str) -> str:
    return "space" + name if name[0].isdigit() else name


def generate(tokens: dict[str, Any], source_label: str) -> str:
    lines = [
        "// GENERATED FILE — do not edit.",
        f"// Source: {source_label}",
        "// Regenerate with `make design-build`; `make design-build-check` fails on drift.",
        "",
        "import CoreGraphics",
        "",
        "public enum DesignTokens {",
        "    public enum Color {",
    ]
    for layer in ("semantic", "component"):
        for name, token in leaves(tokens["color"][layer]):
            light, dark = color_pair(tokens, token)
            lines.append(f"        public static let {name} = TokenColor(light: {light}, dark: {dark})")
    lines += ["    }", "", "    public enum FontSize {"]
    for name, token in leaves(tokens["font"]["size"]):
        lines.append(f"        public static let {name}: CGFloat = {dimension(token)}")
    lines += ["    }", "", "    public enum Space {"]
    for name, token in leaves(tokens["space"]):
        lines.append(f"        public static let s{name}: CGFloat = {dimension(token)}")
    lines += ["    }", "", "    public enum Radius {"]
    for name, token in leaves(tokens["radius"]):
        lines.append(f"        public static let {name}: CGFloat = {dimension(token)}")
    lines += ["    }", "", "    public enum Duration {"]
    for name, token in leaves(tokens["duration"]):
        lines.append(f"        public static let {name}: Double = {dimension(token)} / 1000")
    lines += ["    }", "", "    public enum Layout {"]
    for name, token in leaves(tokens["layout"]):
        lines.append(f"        public static let {name}: CGFloat = {dimension(token)}")
    lines += ["    }", "}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    tokens = json.loads(args.source.read_text(encoding="utf-8"))
    rendered = generate(tokens, "macos/design/tokens.json")
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(f"design-build-check: {args.output} drifts from {args.source}; run `make design-build`", file=sys.stderr)
            return 1
        print("design-build-check: generated tokens match canonical source")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"design-build: wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
