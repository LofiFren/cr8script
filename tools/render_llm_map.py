#!/usr/bin/env python3
"""Render a cr8script LLM map JSON file as a self-contained HTML/SVG page."""

from __future__ import annotations

import html
import json
import math
import sys
from pathlib import Path


LANES = [
    "goal",
    "input",
    "transform",
    "decision",
    "risk",
    "output",
    "check",
    "artifact",
]

LANE_TITLES = {
    "goal": "Goal",
    "input": "Inputs",
    "transform": "Transforms",
    "decision": "Decisions",
    "risk": "Risks",
    "output": "Outputs",
    "check": "Checks",
    "artifact": "Artifacts",
}

LANE_COLORS = {
    "goal": "#e7a75b",
    "input": "#7ab6d8",
    "transform": "#70c19f",
    "decision": "#9aa4ff",
    "risk": "#d17d7d",
    "output": "#c69fdf",
    "check": "#e6c86e",
    "artifact": "#c8c2a8",
}


def load_map(path: Path) -> dict:
    with path.open() as f:
        data = json.load(f)
    validate_map(data)
    return data


def validate_map(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    for key in ("title", "task_kind", "summary", "nodes", "edges"):
        if key not in data:
            raise ValueError(f"missing required key `{key}`")
    if not isinstance(data["nodes"], list) or not isinstance(data["edges"], list):
        raise ValueError("`nodes` and `edges` must be lists")

    seen = set()
    for node in data["nodes"]:
        if not isinstance(node, dict):
            raise ValueError("each node must be an object")
        for key in ("id", "kind", "label"):
            if key not in node:
                raise ValueError(f"node missing required key `{key}`")
        node_id = node["id"]
        if node_id in seen:
            raise ValueError(f"duplicate node id `{node_id}`")
        seen.add(node_id)
        if node["kind"] not in LANE_TITLES:
            raise ValueError(f"unknown node kind `{node['kind']}`")

    for edge in data["edges"]:
        if not isinstance(edge, dict):
            raise ValueError("each edge must be an object")
        for key in ("from", "to", "kind"):
            if key not in edge:
                raise ValueError(f"edge missing required key `{key}`")
        if edge["from"] not in seen:
            raise ValueError(f"edge references unknown source `{edge['from']}`")
        if edge["to"] not in seen:
            raise ValueError(f"edge references unknown target `{edge['to']}`")


def estimate_card_height(node: dict) -> int:
    details = node.get("details") or []
    tags = node.get("tags") or []
    return 88 + len(details) * 18 + (24 if tags else 0)


def layout_nodes(data: dict) -> tuple[dict[str, dict], int, int]:
    lane_width = 240
    lane_gap = 22
    card_gap = 18
    top = 190

    positions: dict[str, dict] = {}
    max_height = top
    for lane_i, lane in enumerate(LANES):
        x = 60 + lane_i * (lane_width + lane_gap)
        y = top
        lane_nodes = [n for n in data["nodes"] if n["kind"] == lane]
        for node in lane_nodes:
            h = estimate_card_height(node)
            positions[node["id"]] = {
                "x": x,
                "y": y,
                "w": lane_width,
                "h": h,
                "lane": lane,
            }
            y += h + card_gap
        max_height = max(max_height, y)

    width = 60 + len(LANES) * lane_width + (len(LANES) - 1) * lane_gap + 60
    height = max(860, max_height + 60)
    return positions, width, height


def edge_path(a: dict, b: dict) -> str:
    x1 = a["x"] + a["w"]
    y1 = a["y"] + a["h"] / 2
    x2 = b["x"]
    y2 = b["y"] + b["h"] / 2
    dx = max(48, (x2 - x1) * 0.45)
    return f"M{x1:.1f},{y1:.1f} C{x1+dx:.1f},{y1:.1f} {x2-dx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"


def render_node(node: dict, pos: dict) -> str:
    color = LANE_COLORS[node["kind"]]
    details = node.get("details") or []
    tags = node.get("tags") or []
    lines: list[str] = []
    lines.append(
        f"<rect x='{pos['x']}' y='{pos['y']}' width='{pos['w']}' height='{pos['h']}' rx='20' class='card card-{node['kind']}' />"
    )
    lines.append(
        f"<rect x='{pos['x'] + 16}' y='{pos['y'] + 16}' width='54' height='22' rx='999' fill='{color}' opacity='0.18' />"
    )
    lines.append(
        f"<text x='{pos['x'] + 24}' y='{pos['y'] + 31}' class='kind'>{html.escape(node['kind'])}</text>"
    )
    lines.append(
        f"<text x='{pos['x'] + 16}' y='{pos['y'] + 60}' class='label'>{html.escape(node['label'])}</text>"
    )
    dy = pos["y"] + 84
    for detail in details:
        lines.append(
            f"<text x='{pos['x'] + 18}' y='{dy}' class='detail'>* {html.escape(detail)}</text>"
        )
        dy += 18
    if tags:
        tag_x = pos["x"] + 16
        tag_y = pos["y"] + pos["h"] - 18
        for tag in tags[:4]:
            lines.append(
                f"<text x='{tag_x}' y='{tag_y}' class='tag'>#{html.escape(tag)}</text>"
            )
            tag_x += 58 + min(len(tag) * 5, 56)
    return "\n".join(lines)


def render(data: dict) -> str:
    positions, width, height = layout_nodes(data)
    nodes_by_id = {n["id"]: n for n in data["nodes"]}

    edge_parts: list[str] = []
    for edge in data["edges"]:
        a = positions[edge["from"]]
        b = positions[edge["to"]]
        lane = nodes_by_id[edge["from"]]["kind"]
        color = LANE_COLORS.get(lane, "#8ea0bb")
        label = edge.get("label", edge["kind"])
        mx = (a["x"] + a["w"] + b["x"]) / 2
        my = ((a["y"] + a["h"] / 2) + (b["y"] + b["h"] / 2)) / 2
        edge_parts.append(
            "\n".join(
                [
                    f"<path d=\"{edge_path(a, b)}\" stroke='{color}' stroke-width='2' fill='none' opacity='0.42' marker-end='url(#arrow)' />",
                    f"<text x='{mx:.1f}' y='{my - 6:.1f}' class='edge-label'>{html.escape(label)}</text>",
                ]
            )
        )

    lane_parts: list[str] = []
    for lane_i, lane in enumerate(LANES):
        x = 60 + lane_i * (240 + 22)
        lane_parts.append(
            "\n".join(
                [
                    f"<rect x='{x - 8}' y='136' width='256' height='{height - 176}' rx='28' class='lane-bg' />",
                    f"<text x='{x}' y='118' class='lane-title'>{html.escape(LANE_TITLES[lane])}</text>",
                ]
            )
        )

    node_parts = [render_node(node, positions[node["id"]]) for node in data["nodes"]]

    data_json = html.escape(json.dumps(data, indent=2))
    title = html.escape(data["title"])
    task_kind = html.escape(data["task_kind"])
    summary = html.escape(data["summary"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} * LLM map</title>
  <style>
    :root {{
      --bg-top: #0d1528;
      --bg-bottom: #0a0f1d;
      --panel: rgba(255, 249, 235, 0.92);
      --ink: #182033;
      --muted: #60708a;
      --line: rgba(255,255,255,0.08);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
      color: #eef2fb;
      background:
        radial-gradient(circle at top, rgba(130,160,220,0.28), transparent 26%),
        linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
      padding: 24px;
    }}
    .shell {{
      display: grid;
      gap: 18px;
      width: min(100%, 2120px);
      margin: 0 auto;
    }}
    .intro {{
      display: grid;
      gap: 10px;
      padding: 18px 22px;
      border-radius: 24px;
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.12);
      box-shadow: 0 18px 48px rgba(0,0,0,0.28);
      backdrop-filter: blur(10px);
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: #d3deff;
      font-weight: 700;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 3.4rem);
      line-height: 0.94;
      font-family: Georgia, "Times New Roman", serif;
      letter-spacing: -0.03em;
    }}
    .summary {{
      max-width: 1050px;
      color: rgba(238,242,251,0.86);
      font-size: 16px;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 13px;
    }}
    .meta span {{
      border-radius: 999px;
      padding: 7px 10px;
      background: rgba(255,255,255,0.1);
      border: 1px solid rgba(255,255,255,0.12);
    }}
    .canvas-card {{
      overflow: auto;
      border-radius: 26px;
      background: linear-gradient(180deg, rgba(255,255,255,0.12), rgba(255,255,255,0.08));
      border: 1px solid rgba(255,255,255,0.12);
      box-shadow: 0 24px 64px rgba(0,0,0,0.28);
      padding: 16px;
    }}
    svg {{
      display: block;
      width: 100%;
      min-width: 1400px;
      height: auto;
      background:
        radial-gradient(circle at top, rgba(255,255,255,0.04), transparent 26%),
        linear-gradient(180deg, rgba(13,21,40,0.94), rgba(10,15,29,0.98));
      border-radius: 20px;
    }}
    .lane-title {{
      fill: #f5f0e2;
      font-size: 16px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .lane-bg {{
      fill: rgba(255,255,255,0.03);
      stroke: rgba(255,255,255,0.07);
      stroke-width: 1;
    }}
    .card {{
      fill: var(--panel);
      stroke: rgba(24,32,51,0.12);
      stroke-width: 1;
      filter: drop-shadow(0 16px 22px rgba(0,0,0,0.16));
    }}
    .kind {{
      fill: #433929;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.09em;
      text-transform: uppercase;
    }}
    .label {{
      fill: var(--ink);
      font-size: 18px;
      font-weight: 800;
    }}
    .detail {{
      fill: #31405a;
      font-size: 13px;
    }}
    .tag {{
      fill: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }}
    .edge-label {{
      fill: rgba(236,241,251,0.82);
      font-size: 11px;
      text-anchor: middle;
      paint-order: stroke;
      stroke: rgba(10,15,29,0.9);
      stroke-width: 4px;
      stroke-linejoin: round;
    }}
    details {{
      border-radius: 18px;
      background: rgba(255,255,255,0.07);
      border: 1px solid rgba(255,255,255,0.1);
      padding: 12px 14px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 700;
    }}
    pre {{
      margin: 12px 0 0;
      white-space: pre-wrap;
      color: #dbe5ff;
      font-size: 12px;
      line-height: 1.45;
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="intro">
      <div class="eyebrow">cr8script LLM map prototype</div>
      <h1>{title}</h1>
      <div class="summary">{summary}</div>
      <div class="meta">
        <span>task kind: <b>{task_kind}</b></span>
        <span>{len(data["nodes"])} nodes</span>
        <span>{len(data["edges"])} edges</span>
        <span>2D typed planning graph</span>
      </div>
    </section>
    <section class="canvas-card">
      <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="LLM map for {title}">
        <defs>
          <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse">
            <path d="M0,0 L12,6 L0,12 Z" fill="rgba(235,240,252,0.85)" />
          </marker>
        </defs>
        {"".join(lane_parts)}
        {"".join(edge_parts)}
        {"".join(node_parts)}
      </svg>
    </section>
    <details>
      <summary>Source JSON</summary>
      <pre>{data_json}</pre>
    </details>
  </main>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: render_llm_map.py <map.json> <out.html>", file=sys.stderr)
        return 2
    in_path = Path(argv[1])
    out_path = Path(argv[2])
    data = load_map(in_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(data))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
