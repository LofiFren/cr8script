#!/usr/bin/env python3
"""Validate a cr8script LLM map JSON file (and optionally check map<->code drift).

The output shape mirrors cr8script's own `--check-json` so an agent can use
the same loop pattern: each issue is `{severity, path, message, hint?}`.
Exit code is non-zero when any error-severity issue is reported.

Usage:

    python3 tools/check_map.py examples/llm_map/balloon_game.map.json
    python3 tools/check_map.py path/to/file.map.json --json
    python3 tools/check_map.py path/to/file.map.json \\
        --drift examples/make_balloon_game.cr8

The drift check looks for `# llmmap: <node_id>` comments in cr8 source and
flags two failure modes:

  - structural map nodes (transform / decision / artifact / check) with no
    matching annotation in any code file
  - `# llmmap:` comments that reference an id absent from the map

(Note: this lives in Python because cr8script does not yet have file I/O.
Once it does, fold the logic into `cr8script.py --check-map`.)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NODE_KINDS = frozenset({
    "goal", "input", "transform", "decision",
    "risk", "output", "check", "artifact",
})
EDGE_KINDS = frozenset({
    "feeds", "uses", "guards", "produces", "verifies", "risks",
})
# Node kinds the drift checker expects to find a `# llmmap:` annotation for.
# Goals / inputs / outputs / risks are organizational and don't have a
# direct code site.
DRIFT_REQUIRED_KINDS = frozenset({"transform", "decision", "artifact", "check"})

LLMMAP_COMMENT = re.compile(r"#\s*llmmap:\s*([A-Za-z_][A-Za-z0-9_-]*)")


def _issue(severity: str, path: str, message: str, hint: str | None = None) -> dict:
    out = {"severity": severity, "path": path, "message": message}
    if hint:
        out["hint"] = hint
    return out


def validate_schema(m: dict) -> list[dict]:
    """Required fields, valid kinds, list-vs-record types."""
    issues: list[dict] = []
    for k in ("title", "task_kind", "summary"):
        if k not in m:
            issues.append(_issue("error", k, f"missing required field `{k}`"))
        elif not isinstance(m[k], str):
            issues.append(_issue("error", k, f"`{k}` must be a string"))
    if not isinstance(m.get("nodes"), list):
        issues.append(_issue("error", "nodes", "`nodes` must be a list"))
        return issues
    if not isinstance(m.get("edges"), list):
        issues.append(_issue("error", "edges", "`edges` must be a list"))
        return issues
    for i, n in enumerate(m["nodes"]):
        if not isinstance(n, dict):
            issues.append(_issue("error", f"nodes[{i}]", "node must be a record"))
            continue
        for k in ("id", "kind", "label"):
            if k not in n:
                issues.append(_issue("error", f"nodes[{i}].{k}",
                                     f"node missing `{k}`"))
        kind = n.get("kind")
        if kind is not None and kind not in NODE_KINDS:
            issues.append(_issue(
                "error", f"nodes[{i}].kind",
                f"`{kind}` is not a valid node kind",
                hint=f"valid: {', '.join(sorted(NODE_KINDS))}"))
    for i, e in enumerate(m["edges"]):
        if not isinstance(e, dict):
            issues.append(_issue("error", f"edges[{i}]", "edge must be a record"))
            continue
        for k in ("from", "to", "kind"):
            if k not in e:
                issues.append(_issue("error", f"edges[{i}].{k}",
                                     f"edge missing `{k}`"))
        kind = e.get("kind")
        if kind is not None and kind not in EDGE_KINDS:
            issues.append(_issue(
                "error", f"edges[{i}].kind",
                f"`{kind}` is not a valid edge kind",
                hint=f"valid: {', '.join(sorted(EDGE_KINDS))}"))
    return issues


def validate_structure(m: dict) -> list[dict]:
    """Cross-cutting structural rules: ids unique, edges connect, checks
    are reached, nodes aren't orphaned."""
    issues: list[dict] = []
    nodes = m.get("nodes", [])
    edges = m.get("edges", [])
    by_id: dict[str, tuple[int, dict]] = {}
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if not isinstance(nid, str):
            continue
        if nid in by_id:
            issues.append(_issue(
                "error", f"nodes[{i}].id",
                f"duplicate node id `{nid}`",
                hint=f"first defined at nodes[{by_id[nid][0]}]"))
        else:
            by_id[nid] = (i, n)

    goals = [n for n in nodes if n.get("kind") == "goal"]
    if not goals:
        issues.append(_issue(
            "error", "nodes",
            "no `goal` node",
            hint="every map should have at least one node of kind `goal`"))
    elif len(goals) > 1:
        issues.append(_issue(
            "warning", "nodes",
            f"{len(goals)} `goal` nodes",
            hint="a map typically has one goal; collapse or split the file"))

    incoming: dict[str, list] = {nid: [] for nid in by_id}
    outgoing: dict[str, list] = {nid: [] for nid in by_id}
    for i, e in enumerate(edges):
        f = e.get("from")
        t = e.get("to")
        if isinstance(f, str) and f not in by_id:
            issues.append(_issue("error", f"edges[{i}].from",
                                 f"`{f}` is not a node id"))
        elif isinstance(f, str):
            outgoing[f].append((i, e))
        if isinstance(t, str) and t not in by_id:
            issues.append(_issue("error", f"edges[{i}].to",
                                 f"`{t}` is not a node id"))
        elif isinstance(t, str):
            incoming[t].append((i, e))

    for nid, (i, n) in by_id.items():
        kind = n.get("kind")
        if kind == "check" and not incoming.get(nid):
            issues.append(_issue(
                "warning", f"nodes[{i}].id",
                f"`check` node `{nid}` has no incoming edge",
                hint="checks should be the target of `verifies` from risks/artifacts"))
        if kind == "output" and not incoming.get(nid):
            issues.append(_issue(
                "warning", f"nodes[{i}].id",
                f"`output` node `{nid}` has no incoming edge",
                hint="outputs should be `produces`-targets of transforms or artifacts"))
        if kind == "artifact" and not (incoming.get(nid) or outgoing.get(nid)):
            issues.append(_issue(
                "warning", f"nodes[{i}].id",
                f"`artifact` node `{nid}` is unconnected"))
        if kind != "goal" and not incoming.get(nid) and not outgoing.get(nid):
            issues.append(_issue(
                "warning", f"nodes[{i}].id",
                f"node `{nid}` has no edges (orphan)"))

    return issues


def check_drift(m: dict, code_paths: list[Path]) -> list[dict]:
    """Compare structural node ids against `# llmmap: <id>` comments in code."""
    issues: list[dict] = []
    nodes = m.get("nodes", [])
    node_ids = {n.get("id") for n in nodes if isinstance(n.get("id"), str)}

    annotated: dict[str, list[tuple[Path, int]]] = {}
    for p in code_paths:
        try:
            text = p.read_text()
        except FileNotFoundError:
            issues.append(_issue("error", str(p), "code file not found"))
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            m2 = LLMMAP_COMMENT.search(line)
            if m2:
                annotated.setdefault(m2.group(1), []).append((p, lineno))

    structural = {
        n.get("id") for n in nodes
        if n.get("kind") in DRIFT_REQUIRED_KINDS and isinstance(n.get("id"), str)
    }
    for nid in sorted(structural - set(annotated.keys())):
        issues.append(_issue(
            "warning", f"map.{nid}",
            f"map node `{nid}` is not implemented in any annotated code",
            hint=f"add `# llmmap: {nid}` near the corresponding cr8 code"))

    for nid, locs in sorted(annotated.items()):
        if nid not in node_ids:
            for p, lineno in locs:
                hint_ids = ", ".join(sorted(node_ids))
                if len(hint_ids) > 120:
                    hint_ids = hint_ids[:117] + "..."
                issues.append(_issue(
                    "error", f"{p}:{lineno}",
                    f"`# llmmap: {nid}` references unknown node id",
                    hint=f"map node ids: {hint_ids}"))

    return issues


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("map_path", type=Path, help="path to .map.json")
    ap.add_argument(
        "--drift", type=Path, action="append", default=[], metavar="PATH",
        help="cr8 source file to scan for `# llmmap: <id>` (repeatable)")
    ap.add_argument(
        "--json", action="store_true",
        help="emit JSON to stdout (default human-readable to stderr)")
    args = ap.parse_args(argv[1:])

    try:
        m = json.loads(args.map_path.read_text())
    except FileNotFoundError:
        print(f"{args.map_path}: not found", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        out = [_issue("error", str(args.map_path),
                      f"invalid JSON: {e.msg}",
                      hint=f"line {e.lineno} col {e.colno}")]
        if args.json:
            print(json.dumps(out))
        else:
            for it in out:
                print(f"{args.map_path}: ERROR ({it['path']}): {it['message']}",
                      file=sys.stderr)
                if "hint" in it:
                    print(f"  hint: {it['hint']}", file=sys.stderr)
        return 1

    issues: list[dict] = []
    issues.extend(validate_schema(m))
    # Structural checks are robust to per-field errors as long as nodes
    # and edges are lists, so run both when possible.
    if isinstance(m.get("nodes"), list) and isinstance(m.get("edges"), list):
        issues.extend(validate_structure(m))
        if args.drift:
            issues.extend(check_drift(m, args.drift))

    has_error = any(i["severity"] == "error" for i in issues)

    if args.json:
        print(json.dumps(issues))
    else:
        if not issues:
            print(f"{args.map_path}: ok - no issues", file=sys.stderr)
        for it in issues:
            sev = it["severity"].upper()
            print(f"{args.map_path}: {sev} ({it['path']}): {it['message']}",
                  file=sys.stderr)
            if "hint" in it:
                print(f"  hint: {it['hint']}", file=sys.stderr)

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
