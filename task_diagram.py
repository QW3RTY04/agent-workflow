"""Deterministic, dependency-free SVG renderer for workflow task graphs."""

from collections import defaultdict, deque
from html import escape
import json
import re


MAX_NODES = 60
NODE_WIDTH = 240
NODE_HEIGHT = 126
H_GAP = 100
V_GAP = 54


def validate(spec):
    """Validate a task graph, raising ValueError with a useful explanation."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    nodes = spec.get("nodes")
    edges = spec.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("nodes and edges must be lists")
    if not nodes:
        raise ValueError("graph must contain at least one node")
    if len(nodes) > MAX_NODES:
        raise ValueError(f"graph exceeds the {MAX_NODES}-node limit")
    title = spec.get("title", "Workflow task diagram")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("title must be a non-empty string of at most 200 characters")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("each node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", node_id):
            raise ValueError("node ids must match [a-z][a-z0-9_-]{0,63}")
        label = node.get("label", node_id)
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("node labels must be non-empty strings of at most 120 characters")
        if node_id in ids:
            raise ValueError(f"duplicate node id: {node_id}")
        ids.add(node_id)
    adjacency = defaultdict(list)
    indegree = {node_id: 0 for node_id in ids}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("each edge must be an object")
        source, target = edge.get("from"), edge.get("to")
        if not isinstance(source, str) or not isinstance(target, str) or source not in ids or target not in ids:
            raise ValueError(f"edge references unknown node: {source!r} -> {target!r}")
        if "rework" in edge and not isinstance(edge["rework"], bool):
            raise ValueError("edge rework must be a boolean")
        if source == target and not edge.get("rework", False):
            raise ValueError("self dependency must be marked rework")
        if not edge.get("rework", False):
            adjacency[source].append(target)
            indegree[target] += 1
    queue = deque(node_id for node_id in ids if indegree[node_id] == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(ids):
        raise ValueError("dependency cycle detected; only rework edges may cycle")
    return True


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def render(spec):
    """Return a standalone HTML document containing a pan/zoom SVG graph."""
    validate(spec)
    nodes, edges = spec.get("nodes", []), spec.get("edges", [])
    by_id = {node["id"]: node for node in nodes}
    deps = defaultdict(list)
    indegree = {node["id"]: 0 for node in nodes}
    for edge in edges:
        if not edge.get("rework", False):
            deps[edge["from"]].append(edge["to"])
            indegree[edge["to"]] += 1
    rank = {node_id: 0 for node_id, degree in indegree.items() if degree == 0}
    queue = deque(rank)
    while queue:
        current = queue.popleft()
        for target in deps[current]:
            rank[target] = max(rank.get(target, 0), rank[current] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    # Stable ordering within columns is based on input order.
    columns = defaultdict(list)
    for node in nodes:
        columns[rank.get(node["id"], 0)].append(node["id"])
    max_rows = max((len(col) for col in columns.values()), default=1)
    max_rank = max(columns, default=0)
    pad = 54
    width = pad * 2 + (max_rank + 1) * NODE_WIDTH + max_rank * H_GAP
    height = pad * 2 + max_rows * NODE_HEIGHT + (max_rows - 1) * V_GAP
    positions = {}
    for col_rank, col in columns.items():
        for row, node_id in enumerate(col):
            positions[node_id] = (pad + col_rank * (NODE_WIDTH + H_GAP), pad + row * (NODE_HEIGHT + V_GAP))

    def e(value):
        return escape(_text(value), quote=True)

    parts = []
    for edge in edges:
        x1, y1 = positions[edge["from"]]
        x2, y2 = positions[edge["to"]]
        sx, sy = x1 + NODE_WIDTH, y1 + NODE_HEIGHT / 2
        tx, ty = x2, y2 + NODE_HEIGHT / 2
        if edge.get("rework", False):
            path = f"M {sx} {sy} C {sx + 40} {sy - 70}, {tx - 40} {ty - 70}, {tx} {ty}"
            style = 'stroke="#e8b765" stroke-dasharray="7 6"'
        else:
            bend = (sx + tx) / 2
            path = f"M {sx} {sy} C {bend} {sy}, {bend} {ty}, {tx} {ty}"
            style = 'stroke="#526782"'
        parts.append(f'<path d="{path}" fill="none" {style} stroke-width="2" marker-end="url(#arrow)"/>')
    for node in nodes:
        node_id = node["id"]
        x, y = positions[node_id]
        label = _text(node.get("label", node_id))
        context_parts = []
        for key in ("lane", "human_step", "output", "checks"):
            value = node.get(key)
            if value not in (None, "", [], {}):
                context_parts.append(f"{key.replace('_', ' ')}: {_text(value)}")
        context = " · ".join(context_parts)
        parts.append(
            f'<g id="node-{e(node_id)}" data-node-id="{e(node_id)}" data-node-label="{e(label)}" data-detail="{e(context)}">'
            f'<rect x="{x}" y="{y}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" rx="12" fill="#142033" stroke="#526782" stroke-width="2"/>'
            f'<text data-node-label="{e(label)}" x="{x + NODE_WIDTH/2}" y="{y + 32}" text-anchor="middle" fill="#edf4ff" font-family="system-ui,sans-serif" font-size="16" font-weight="650">{e(label)}</text>'
            f'<text data-detail="context" x="{x + 16}" y="{y + 58}" fill="#aabbd1" font-family="system-ui,sans-serif" font-size="12">{e(context)}</text>'
            '</g>'
        )
    title = e(spec.get("title", "Workflow task diagram"))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{title}" style="width:100%;height:100%;touch-action:none">
<defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#263449"/></pattern><marker id="arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0 0L9 3.5L0 7Z" fill="#71849e"/></marker></defs>
<rect width="100%" height="100%" fill="#0a101d"/><rect width="100%" height="100%" fill="url(#dots)"/><g id="viewport">{''.join(parts)}</g></svg>'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>html,body{{width:100%;height:100%;margin:0;background:#0a101d;color:#e6edf5;font:14px system-ui,sans-serif;overflow:hidden}}.diagram-container{{width:100%;height:100%;overflow:hidden}}.diagram-container svg{{display:block}}</style></head><body><div class="diagram-container">{svg}</div>
<script>(function(){{const svg=document.querySelector('svg'),view=document.getElementById('viewport');let scale=1,x=0,y=0,drag=null;function apply(){{view.setAttribute('transform',`translate(${{x}} ${{y}}) scale(${{scale}})`);}}function state(){{return {{scale,x,y}}}}function zoom(factor){{scale=Math.max(.25,Math.min(4,scale*factor));apply();return state()}}function reset(){{scale=1;x=0;y=0;apply();return state()}}window.Archify=window.Archify||{{}};window.Archify.view={{zoomIn:()=>zoom(1.2),zoomOut:()=>zoom(1/1.2),reset,state}};svg.addEventListener('pointerdown',e=>{{if(e.target.closest('[data-node-id]'))return;drag={{x:e.clientX,y:e.clientY,ox:x,oy:y}};svg.setPointerCapture(e.pointerId);}});svg.addEventListener('pointermove',e=>{{if(!drag)return;x=drag.ox+(e.clientX-drag.x);y=drag.oy+(e.clientY-drag.y);apply();}});function end(){{drag=null}}svg.addEventListener('pointerup',end);svg.addEventListener('pointercancel',end);}})();</script></body></html>'''
