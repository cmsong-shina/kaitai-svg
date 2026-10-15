"""Render a portable SVG with optional interactivity and no external assets."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from pathlib import Path
import re

from .layout import EdgeLayout, NodeLayout, RowLayout, layout_graph, text_width
from .placement import LayoutEngine, get_layout_engine
from .model import Graph
from .palette import TYPE_EDGE_COLORS
from .routing import Router
from .visibility import filter_hidden, filter_unused_imports


_CSS = """
svg { background: #f4f7fb; color: #18283e; font-family: 'Segoe UI', 'Noto Sans', 'Malgun Gothic', system-ui, -apple-system, sans-serif; }
.canvas-bg { fill: #f4f7fb; }
.diagram-title { font-size: 25px; font-weight: 700; fill: #18283e; }
.diagram-caption { font-size: 12px; fill: #64758c; }
.diagram-title, .node-subtitle, .edge-label, .edge-label-halo { font-family: 'Segoe UI', 'Noto Sans', 'Malgun Gothic', system-ui, -apple-system, sans-serif; }
.node-title, .field-label, .field-type, .field-pos, .field-size, .field-detail, .type-size { font-family: 'Cascadia Mono', Consolas, 'DejaVu Sans Mono', 'Malgun Gothic', monospace; font-variant-ligatures: none; }
.node-box { fill: #fff; stroke: #cbd5e1; stroke-width: 1.3; filter: url(#card-shadow); }
.node-header { fill: #edf4fb; }
.node-kind-enum .node-header { fill: #f2edfc; }
.node-kind-opaque .node-header { fill: #fff5df; }
.node-accent { fill: #327dba; }
.node-kind-enum .node-accent { fill: #8564bd; }
.node-kind-opaque .node-accent { fill: #b47c17; }
.node-title { fill: #183b5b; font-size: 17px; font-weight: 700; }
.node-subtitle { fill: #61748b; font-size: 11px; }
.size-badge { fill: #fff; stroke: #c9dce9; stroke-width: 1; }
.size-caption { fill: #657c90; font-size: 9px; font-weight: 600; letter-spacing: .7px; }
.type-size { fill: #185c84; font-size: 12px; font-weight: 700; }
.file-group-box { fill: #eaf0f7; fill-opacity: .55; stroke: #b9cbdc; stroke-width: 1.5; stroke-dasharray: 8 4; }
.file-group-title { fill: #3d5873; font-size: 15px; font-weight: 700; }
.file-group-count { fill: #6a7d91; font-size: 11px; }
.section-band { fill: #eaf1f8; }
.section-kind-instance .section-band { fill: #f0eaf8; }
.section-kind-param .section-band { fill: #f8f1e3; }
.section-label { fill: #385870; font-size: 11px; font-weight: 700; }
.section-count { fill: #718397; font-size: 10px; }
.table-header { fill: #f8fafc; }
.table-heading { fill: #6a7b8d; font-size: 11px; font-weight: 600; }
.column-divider { stroke: #e7edf3; stroke-width: 1; pointer-events: none; }
.row-hit { fill: transparent; }
.row-divider { stroke: #e5ebf3; stroke-width: 1; }
.field-label { font-size: 13px; font-weight: 600; fill: #253b53; }
.field-type { font-size: 12px; fill: #216a88; }
.field-pos { font-size: 12px; fill: #677e92; font-variant-numeric: tabular-nums; }
.field-size { text-anchor: end; font-size: 12px; font-weight: 600; fill: #185c84; font-variant-numeric: tabular-nums; }
.field-detail { font-size: 11px; fill: #718096; }
.row-kind-instance .field-label { fill: #8251a8; }
.row-kind-param .field-label { fill: #9a6712; }
.doc-dot { fill: #5987ad; }
.empty-label { font-size: 11px; font-style: italic; fill: #8995a6; }
.edge-hit { fill: none; stroke: transparent; stroke-width: 12; pointer-events: stroke; }
.edge-halo { fill: none; stroke: #f4f7fb; stroke-width: 6; stroke-linejoin: round; pointer-events: none; }
.edge-path, .legend-path { fill: none; stroke: #6593af; stroke-width: 1.8; stroke-linejoin: round; stroke-linecap: round; pointer-events: none; }
.edge-kind-enum .edge-path { stroke: #9a81bc; }
.edge-kind-import .edge-path, .edge-kind-import .legend-path { stroke: #b66a16; stroke-width: 2; stroke-dasharray: 9 5; }
.edge-kind-contains .edge-path, .edge-kind-contains .legend-path { stroke: #177d72; stroke-width: 1.8; stroke-dasharray: none; }
.edge-label, .edge-label-halo { font-size: 11px; fill: #36576d; opacity: 0; pointer-events: none; }
.edge-label-halo { stroke: #f4f7fb; stroke-width: 4px; stroke-linejoin: round; }
.legend text { font-size: 11px; fill: #64758c; }
.port { fill: #6593af; }
.edge-kind-enum .port { fill: #9a81bc; }
.edge-kind-import .port { fill: #b66a16; }
"""


_INTERACTIVE_CSS = """
svg { touch-action: none; }
#viewport { user-select: none; -webkit-user-select: none; }
.node, .edge { outline: none; transition: opacity .12s; }
.row { outline: none; }
.row:hover .row-hit, .row:focus .row-hit { fill: #e8f3ff; }
.node:focus > .node-box, .node.active > .node-box { stroke: #137dc5; stroke-width: 2.5; }
.node.muted, .edge.muted { opacity: .23; }
.edge.active .edge-path { stroke-width: 3; }
.edge.active .edge-label, .edge.active .edge-label-halo { opacity: 1; }
.controls { display: none; }
.control { cursor: pointer; outline: none; }
.control rect { fill: #fff; stroke: #c3cfde; stroke-width: 1; }
.control:hover rect, .control:focus rect { fill: #dfedfa; stroke: #327dba; }
.control text { fill: #294a69; font-size: 19px; text-anchor: middle; dominant-baseline: central; pointer-events: none; }
.control .reset-text { font-size: 11px; font-weight: 600; }
.tooltip-layer { visibility: hidden; }
.tooltip-box { box-sizing: border-box; background: #172c44; color: #f4f8ff; border: 1px solid #354d68; border-radius: 9px; padding: 13px 16px; box-shadow: 0 5px 18px #10253b33; font: 13px/1.55 'Segoe UI', 'Noto Sans', 'Malgun Gothic', system-ui, -apple-system, sans-serif; white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; user-select: text; }
@media (prefers-reduced-motion: reduce) { .node, .edge { transition: none; } }
"""


_SCRIPT = r"""
(() => {
  'use strict';
  const svg = document.currentScript.ownerSVGElement;
  if (!svg) return;
  const viewport = svg.querySelector('#viewport');
  const controls = svg.querySelector('#controls');
  const tooltip = svg.querySelector('#tooltip');
  const tipObject = tooltip.querySelector('foreignObject');
  const tipText = tooltip.querySelector('.tooltip-box');
  const nodes = [...svg.querySelectorAll('.node')];
  const edges = [...svg.querySelectorAll('.edge')];
  let view = {x: 0, y: 0, k: 1}, drag = null, active = null, hideTimer;
  function local(clientX, clientY) {
    const point = svg.createSVGPoint(); point.x = clientX; point.y = clientY;
    const matrix = svg.getScreenCTM();
    return matrix ? point.matrixTransform(matrix.inverse()) : point;
  }
  function apply() { viewport.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`); }
  function clearHighlight() {
    for (const el of [...nodes, ...edges]) el.classList.remove('muted', 'active');
  }
  function hide() {
    tooltip.style.visibility = 'hidden'; active = null; clearHighlight();
  }
  function deferHide() { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 180); }
  function highlight(item) {
    const node = item.closest('.node');
    const selectedEdge = item.closest('.edge');
    if (!node && !selectedEdge) return;
    const id = node?.dataset.nodeId, row = item.closest('.row');
    const connected = new Set(node ? [id] : [selectedEdge.dataset.source, selectedEdge.dataset.target]);
    for (const edge of edges) {
      const hit = selectedEdge ? edge === selectedEdge : row
        ? edge.dataset.source === id && edge.dataset.sourceRow === row.dataset.rowId
        : edge.dataset.source === id || edge.dataset.target === id;
      edge.classList.toggle('active', hit); edge.classList.toggle('muted', !hit);
      if (hit) { connected.add(edge.dataset.source); connected.add(edge.dataset.target); }
    }
    for (const other of nodes) {
      other.classList.toggle('active', selectedEdge ? connected.has(other.dataset.nodeId) : other === node);
      other.classList.toggle('muted', !connected.has(other.dataset.nodeId));
    }
  }
  function positionTip(clientX, clientY) {
    const bounds = svg.getBoundingClientRect(), matrix = svg.getScreenCTM();
    if (!matrix) return;
    const width = Math.max(120, Math.min(440, bounds.width - 32));
    const maximum = Math.max(80, Math.min(440, bounds.height - 40));
    tipObject.setAttribute('width', width);
    tipText.style.maxHeight = `${maximum}px`;
    tipText.style.width = `${width}px`;
    const height = Math.min(maximum, tipText.scrollHeight + 2);
    tipObject.setAttribute('height', height);
    let x = clientX + 18, y = clientY + 16;
    if (x + width > bounds.right - 12) x = clientX - width - 18;
    x = Math.max(bounds.left + 12, Math.min(x, bounds.right - width - 12));
    y = Math.max(bounds.top + 12, Math.min(y, bounds.bottom - height - 12));
    const point = local(x, y), scale = 1 / Math.hypot(matrix.a, matrix.b);
    tooltip.setAttribute('transform', `translate(${point.x} ${point.y}) scale(${scale})`);
  }
  function show(item, clientX, clientY) {
    if (!item || drag?.moved) return;
    clearTimeout(hideTimer);
    if (active !== item) {
      active = item; tipText.textContent = item.dataset.tooltip;
      tipText.scrollTop = 0; highlight(item);
    }
    tooltip.style.visibility = 'visible';
    positionTip(clientX, clientY);
  }
  function arrangeControls() {
    const bounds = svg.getBoundingClientRect(), matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = local(bounds.right - 154, bounds.top + 16);
    controls.setAttribute('transform', `translate(${point.x} ${point.y}) scale(${1 / Math.hypot(matrix.a, matrix.b)})`);
    controls.style.display = 'inline';
  }
  function zoom(factor, clientX, clientY) {
    const point = local(clientX, clientY), next = Math.max(.12, Math.min(12, view.k * factor));
    const ratio = next / view.k;
    view.x = point.x - (point.x - view.x) * ratio;
    view.y = point.y - (point.y - view.y) * ratio;
    view.k = next; apply(); hide();
  }
  function action(name) {
    if (name === 'reset') { view = {x: 0, y: 0, k: 1}; apply(); hide(); return; }
    const box = svg.getBoundingClientRect();
    zoom(name === 'in' ? 1.3 : 1 / 1.3, box.left + box.width / 2, box.top + box.height / 2);
  }
  svg.addEventListener('wheel', event => {
    if (event.target.closest('.tooltip-layer')) return;
    event.preventDefault();
    zoom(Math.exp(-Math.max(-150, Math.min(150, event.deltaY)) * .002), event.clientX, event.clientY);
  }, {passive: false});
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('.controls, .tooltip-layer')) return;
    const point = local(event.clientX, event.clientY);
    drag = {id: event.pointerId, x: point.x, y: point.y, originX: view.x, originY: view.y,
      clientX: event.clientX, clientY: event.clientY, moved: false};
  });
  svg.addEventListener('pointermove', event => {
    // A release outside the SVG can precede capture (before the drag threshold).
    // The button state also handles releasing the primary button during chords.
    if (drag?.id === event.pointerId && !(event.buttons & 1)) endDrag(event);
    if (drag && drag.id === event.pointerId) {
      if (!drag.moved && Math.hypot(event.clientX - drag.clientX, event.clientY - drag.clientY) > 4) {
        drag.moved = true; svg.setPointerCapture(event.pointerId); hide();
        svg.style.cursor = 'grabbing';
      }
      if (drag.moved) {
        const point = local(event.clientX, event.clientY);
        view.x = drag.originX + point.x - drag.x; view.y = drag.originY + point.y - drag.y; apply();
        return;
      }
    }
    const item = event.target.closest('[data-tooltip]');
    if (item) show(item, event.clientX, event.clientY);
  });
  function endDrag(event) {
    if (drag?.id !== event.pointerId) return;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    drag = null; svg.style.cursor = '';
  }
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('lostpointercapture', endDrag);
  svg.addEventListener('pointerover', event => {
    const item = event.target.closest('[data-tooltip]');
    if (item) show(item, event.clientX, event.clientY);
  });
  svg.addEventListener('pointerout', event => {
    const next = event.relatedTarget;
    if (!next || !svg.contains(next) || (!next.closest('[data-tooltip]') && !next.closest('.tooltip-layer'))) deferHide();
  });
  tooltip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  tooltip.addEventListener('pointerleave', deferHide);
  svg.addEventListener('focusin', event => {
    const item = event.target.closest('[data-tooltip]');
    if (!item) return;
    const box = item.getBoundingClientRect(); show(item, box.right, box.top);
  });
  svg.addEventListener('focusout', deferHide);
  for (const control of controls.querySelectorAll('[data-action]')) {
    control.addEventListener('click', () => action(control.dataset.action));
    control.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); action(control.dataset.action); }
    });
  }
  svg.addEventListener('keydown', event => {
    if (event.key === 'Escape') hide();
    else if (event.key === '+' || event.key === '=') { event.preventDefault(); action('in'); }
    else if (event.key === '-') { event.preventDefault(); action('out'); }
    else if (event.key === '0' || event.key === 'Home') { event.preventDefault(); action('reset'); }
  });
  window.addEventListener('resize', () => { arrangeControls(); hide(); });
  arrangeControls();
})();
"""


def _vertical_interaction_script() -> str:
    """Adapt canvas controls to a naturally scrolling web document."""
    return (_SCRIPT
            .replace("  function deferHide() { clearTimeout(hideTimer); hideTimer = setTimeout(hide, 180); }",
                     "  function deferHide() { clearTimeout(hideTimer); hideTimer = setTimeout(() => { if (active !== document.activeElement) hide(); }, 180); }")
            .replace("  function local(clientX, clientY) {", """  function visibleBounds() {
    const bounds = svg.getBoundingClientRect();
    const left = Math.max(0, bounds.left), top = Math.max(0, bounds.top);
    const right = Math.min(window.innerWidth, bounds.right), bottom = Math.min(window.innerHeight, bounds.bottom);
    return {left, top, right, bottom, width: right - left, height: bottom - top};
  }
  function local(clientX, clientY) {""")
            .replace("const bounds = svg.getBoundingClientRect(), matrix = svg.getScreenCTM();",
                     "const bounds = visibleBounds(), matrix = svg.getScreenCTM();")
            .replace("    const box = svg.getBoundingClientRect();\n    zoom(",
                     "    const box = visibleBounds();\n    zoom(")
            .replace("    if (event.target.closest('.tooltip-layer')) return;\n    event.preventDefault();",
                     "    if (!(event.ctrlKey || event.metaKey) || event.target.closest('.tooltip-layer')) return;\n    event.preventDefault();")
            .replace("    if (event.button !== 0 || event.target.closest('.controls, .tooltip-layer')) return;",
                     "    if (event.pointerType === 'touch' || event.button !== 0 || event.target.closest('.controls, .tooltip-layer')) return;")
            .replace("  svg.addEventListener('pointerover', event => {\n    const item =",
                     "  svg.addEventListener('pointerover', event => {\n    if (active && document.activeElement === active) return;\n    const item =")
            .replace("  arrangeControls();\n})();",
                     """  window.addEventListener('scroll', event => {
    if (event.target?.closest?.('.tooltip-layer')) return;
    arrangeControls();
    if (active && document.activeElement === active) {
      const box = active.getBoundingClientRect(), bounds = visibleBounds();
      if (box.bottom > bounds.top && box.top < bounds.bottom && box.right > bounds.left && box.left < bounds.right) {
        positionTip(box.right, box.top); return;
      }
    }
    hide();
  }, {passive: true, capture: true});
  arrangeControls();
})();"""))


def _xml(value: object) -> str:
    # XML 1.0 rejects some Unicode code points accepted in Python strings.
    value = re.sub(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]", "\ufffd", str(value))
    return escape(value, quote=True)


def _attr(value: object) -> str:
    """Preserve whitespace that XML would otherwise normalize in attributes."""
    return _xml(value).replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")


def _number(value: float) -> str:
    return f"{value:g}"


def _kind(value: str) -> str:
    return value if value in {"type", "enum", "opaque", "field", "param", "instance", "contains", "import"} else "type"


def _text_lines(lines: list[str], x: float, y: float, step: int, class_name: str) -> str:
    return "".join(f'<text class="{class_name}" x="{_number(x)}" y="{_number(y + index * step)}">{_xml(line)}</text>' for index, line in enumerate(lines))


def _tooltip(*parts: str) -> str:
    return "\n\n".join(str(part) for part in parts if part)


def _interaction_attrs(tip: str, interactive: bool) -> str:
    return f' tabindex="0" data-tooltip="{_attr(tip)}"' if interactive else ""


def _render_node(item: NodeLayout, index: int, *, interactive: bool) -> str:
    node = item.node
    tip = _tooltip(node.title, node.doc, node.subtitle, f"Sequence size: {node.size_label}" if node.size_label else "")
    width, height = _number(item.width), _number(item.height)
    parts = [f'<g class="node node-kind-{_kind(node.kind)}" id="node-{index}" data-node-id="{_xml(node.id)}" data-source-file="{_attr(node.source_file)}" data-size="{_attr(node.size_label)}" role="group" aria-label="{_xml(node.title)}"{_interaction_attrs(tip, interactive)} transform="translate({_number(item.x)} {_number(item.y)})">',
             f'<title>{_xml(tip)}</title>',
             f'<rect class="node-box" width="{width}" height="{height}" rx="10"/>',
             f'<path class="node-header" d="M10 0 H{_number(item.width - 10)} Q{width} 0 {width} 10 V{_number(item.header_height)} H0 V10 Q0 0 10 0 Z"/>',
             f'<rect class="node-accent" x="0" y="12" width="4" height="{_number(max(16, item.header_height - 24))}" rx="2"/>',
             _text_lines(item.title_lines, 16, 24, 20, "node-title"),
             _text_lines(item.subtitle_lines, 16, 24 + 20 * len(item.title_lines), 14, "node-subtitle")]
    if item.size_lines:
        parts.extend([
            f'<rect class="size-badge" x="{_number(item.width - 140)}" y="10" width="124" height="{24 + 15 * len(item.size_lines)}" rx="5"/>',
            f'<text class="size-caption" x="{_number(item.width - 130)}" y="22">SEQ SIZE</text>',
            _text_lines(item.size_lines, item.width - 130, 38, 15, "type-size"),
        ])
    row_indices = {id(row): i for i, row in enumerate(item.rows)}
    for section in item.sections:
        section_kind = {"params": "param", "seq": "field", "instances": "instance", "values": "enum"}.get(section.kind, section.kind)
        parts.extend([
            f'<g class="section section-kind-{_kind(section_kind)}" data-section-kind="{_attr(section.label)}" role="group" aria-label="{_attr(section.label)}">',
            f'<rect class="section-band" y="{_number(section.y)}" width="{width}" height="{_number(section.header_height)}"/>',
            f'<text class="section-label" x="16" y="{_number(section.y + 16)}">{_xml(section.label)}</text>',
            f'<text class="section-count" x="{_number(item.width - 16)}" y="{_number(section.y + 16)}" text-anchor="end">{len(section.rows)} {"entry" if len(section.rows) == 1 else "entries"}</text>',
            f'<rect class="table-header" y="{_number(section.y + section.header_height)}" width="{width}" height="{_number(section.table_header_height)}"/>',
        ])
        for column in item.columns:
            padding = 16 if column.x == 0 else 10
            parts.append(f'<text class="table-heading" data-column="{column.key}" x="{_number(column.x + padding)}" y="{_number(section.y + section.header_height + 15)}">{_xml(column.label)}</text>')
        for row in section.rows:
            parts.append(_render_row(item, row, index, row_indices[id(row)], interactive=interactive))
        parts.append('</g>')
    if not item.rows:
        parts.append(f'<text class="empty-label" x="16" y="{_number(item.header_height + 18)}">No fields</text>')
    parts.append('</g>')
    return "".join(parts)


def _render_row(item: NodeLayout, row: RowLayout, index: int, row_index: int, *, interactive: bool) -> str:
    data = row.row
    row_tip = _tooltip(data.label, data.type_label, data.doc, data.details)
    width = _number(item.width)
    parts = [f'<g class="row row-kind-{_kind(data.kind)}" id="node-{index}-row-{row_index}" data-row-id="{_xml(data.id)}" data-row-kind="{_attr(data.kind)}" data-pos="{_attr(data.pos_label)}" data-size="{_attr(data.size_label)}" role="group" aria-label="{_xml(data.label + ": " + data.type_label)}"{_interaction_attrs(row_tip, interactive)} transform="translate(0 {_number(row.y)})">',
                  f'<title>{_xml(row_tip)}</title>',
                  f'<rect class="row-hit" width="{width}" height="{_number(row.height)}"/>',
                  f'<path class="row-divider" d="M0 0 H{width}"/>']
    cells = {"pos": (row.pos_lines, "field-pos"), "size": (row.size_lines, "field-size"),
             "type": (row.type_lines, "field-type"), "id": (row.label_lines, "field-label")}
    for column in item.columns:
        left = column.x + (16 if column.x == 0 else 10)
        right = column.x + column.width - (16 if column.key == "id" else 10)
        lines, class_name = cells[column.key]
        parts.extend([
            f'<g class="table-cell" data-column="{column.key}" data-cell-left="{_number(left)}" data-cell-right="{_number(right)}">',
            _text_lines(lines, right if column.key == "size" else left, 18, 16, class_name), '</g>',
        ])
        if column.x:
            parts.append(f'<path class="column-divider" d="M{_number(column.x)} 0 V{_number(row.detail_y - 8 if row.detail_lines else row.height)}"/>')
    parts.append(_text_lines(row.detail_lines, 16, row.detail_y, 13, "field-detail"))
    if data.doc:
        parts.append(f'<circle class="doc-dot" cx="{_number(item.width - 7)}" cy="15" r="2.3" aria-hidden="true"/>')
    parts.append('</g>')
    return "".join(parts)


class _EdgeLabelContext:
    """Bounded label placement against complete routes and visible obstacles."""

    def __init__(self, layout, left: float):
        self.bounds = left + 4, 114, layout.width - 4, layout.height - 4
        self.obstacles = [(node.x - 4, node.y - 4, node.x + node.width + 4,
                           node.y + node.height + 4) for node in layout.nodes]
        self.obstacles.extend((group.x + 12, group.y + 4, group.x + group.width - 12,
                               group.y + group.header_height) for group in layout.groups)
        # Four units cover the six-unit route halo and an active three-unit
        # colored stroke with room for fractional coordinates.
        self.routes = [(min(a[0], b[0]) - 4, min(a[1], b[1]) - 4,
                        max(a[0], b[0]) + 4, max(a[1], b[1]) + 4)
                       for edge in layout.edges for a, b in zip(edge.points, edge.points[1:])]
        self.labels = []

    @staticmethod
    def _overlap(first, second):
        return (max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
                * max(0.0, min(first[3], second[3]) - max(first[1], second[1])))

    def position(self, item: EdgeLayout, label: str) -> str:
        segments = list(zip(item.points, item.points[1:]))
        segments = segments[1:-1] or segments
        segments = sorted(segments, key=lambda segment: (
            segment[0][1] != segment[1][1],
            -(abs(segment[0][0] - segment[1][0]) + abs(segment[0][1] - segment[1][1]))))[:8]
        half = text_width(label) * 11 * .67 / 2 + 2
        best = None
        for a, b in segments:
            horizontal = a[1] == b[1]
            axis = 0 if horizontal else 1
            low, high = sorted((a[axis], b[axis]))
            if high - low < 2 * half + 12:
                continue
            locations = ((low + high) / 2, low + half + 8, high - half - 8,
                         low + (high - low) / 4, high - (high - low) / 4)
            for location in dict.fromkeys(locations):
                if not low + half + 4 <= location <= high - half - 4:
                    continue
                for offset in (-10, 18):
                    x, y = (location, a[1] + offset) if horizontal else (a[0] + offset, location)
                    # This conservative box includes the four-unit text halo.
                    box = ((x - half, y - 14, x + half, y + 6) if horizontal else
                           (x - 14, y - half, x + 6, y + half))
                    left, top, right, bottom = self.bounds
                    dx = max(0, left - box[0]) - max(0, box[2] - right)
                    dy = max(0, top - box[1]) - max(0, box[3] - bottom)
                    inside = dx == 0 and dy == 0
                    if not inside:
                        x, y = x + dx, y + dy
                        box = box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy
                    collision = (sum(self._overlap(box, obstacle) for obstacle in self.obstacles) * 4
                                 + sum(self._overlap(box, route) for route in self.routes)
                                 + sum(self._overlap(box, previous) for previous in self.labels) * 2)
                    position = (f'x="{_number(x)}" y="{_number(y)}"' if horizontal else
                                f'transform="translate({_number(x)} {_number(y)}) rotate(-90)"')
                    if inside and collision == 0:
                        self.labels.append(box)
                        return position
                    score = collision, not inside
                    if best is None or score < best[0]:
                        best = score, position, box
        if best is not None:
            self.labels.append(best[2])
            return best[1]
        # The original truncation leaves enough room on its longest segment.
        # This branch also handles custom routers returning very short paths.
        a, b = max(segments, key=lambda segment: abs(segment[0][0] - segment[1][0])
                   + abs(segment[0][1] - segment[1][1]))
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        x = min(self.bounds[2] - half, max(self.bounds[0] + half, x))
        y = min(self.bounds[3] - 6, max(self.bounds[1] + 14, y - 10))
        self.labels.append((x - half, y - 14, x + half, y + 6))
        return f'x="{_number(x)}" y="{_number(y)}"'


def _render_edge(item: EdgeLayout, titles: dict[str, str], *, interactive: bool,
                color_index: int | None = None,
                label_context: _EdgeLabelContext | None = None) -> str:
    edge = item.edge
    path = " ".join(("M" if index == 0 else "L") + _number(x) + " " + _number(y) for index, (x, y) in enumerate(item.points))
    row_attr = f' data-source-row="{_xml(edge.source_row)}"' if edge.source_row is not None else ""
    marker = "arrow-import" if edge.kind == "import" else "arrow-enum" if edge.kind == "enum" else "arrow"
    color_class = ""
    if color_index is not None:
        marker = f"arrow-type-{color_index}"
        color_class = f" type-edge-color-{color_index}"
    markers = 'marker-start="url(#diamond-contains)"' if edge.kind == "contains" else f'marker-end="url(#{marker})"'
    common = f'data-source="{_xml(edge.source)}" data-target="{_xml(edge.target)}"{row_attr}'
    description = _tooltip(f"{titles[edge.source]} → {titles[edge.target]}", edge.label or edge.kind)
    parts = [f'<g class="edge edge-kind-{_kind(edge.kind)}{color_class}" {common} role="group" aria-label="{_xml(description)}"{_interaction_attrs(description, interactive)}>',
             f'<title>{_xml(description)}</title>',
             f'<path class="edge-hit" d="{path}"/>',
             f'<path class="edge-halo" d="{path}"/>',
             f'<path class="edge-path" {common} d="{path}" {markers}/>']
    if item.points and edge.kind != "contains":
        x, y = item.points[0]
        parts.append(f'<circle class="port" cx="{_number(x)}" cy="{_number(y)}" r="2.8"/>')
    if edge.label:
        segments = list(zip(item.points, item.points[1:]))
        # Labels stay in a routing corridor, never inside a node rectangle.
        interior = segments[1:-1] or segments
        horizontal = [segment for segment in interior if segment[0][1] == segment[1][1]]
        candidates = horizontal or interior
        if candidates:
            a, b = max(candidates, key=lambda segment: abs(segment[0][0] - segment[1][0]) + abs(segment[0][1] - segment[1][1]))
            length = abs(a[0] - b[0]) + abs(a[1] - b[1])
            # The full text remains in <title> when a busy corridor is narrow.
            max_chars = max(0, int((length - 16) / (11 * 0.67)))
            label = edge.label.replace("\n", " ")
            while text_width(label) > max_chars and label:
                label = label[:-1]
            if label and label != edge.label.replace("\n", " "):
                label = label[:-1] + "…"
            if label:
                x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                if a[1] == b[1]:
                    position = f'x="{_number(x)}" y="{_number(y - 6)}"'
                else:
                    position = f'transform="translate({_number(x - 6)} {_number(y)}) rotate(-90)"'
                if label_context is not None:
                    position = label_context.position(item, label)
                # Draw the background halo separately so older SVG renderers
                # that ignore CSS paint-order still show readable lettering.
                parts.append(f'<text class="edge-label-halo" {position} text-anchor="middle" aria-hidden="true">{_xml(label)}</text>')
                parts.append(f'<text class="edge-label" {position} text-anchor="middle">{_xml(label)}</text>')
    parts.append('</g>')
    return "".join(parts)


def render_svg(graph: Graph, *, show_imports: bool = False,
               show_nested_types: bool = False, interactive: bool = False,
               color_type_edges: bool = False,
               router: str | type[Router] = "default",
               layout: str | type[LayoutEngine] = "default",
               layout_width: float | None = None,
               hide: Sequence[str | Path] = (), treeshake: bool = False) -> str:
    """Return a static SVG, or opt into browser controls with ``interactive``.

    A native SVG ``title`` is included on every documented element, so the
    diagram still contains all documentation when scripting is unavailable.
    Import and nested definition connections are opt-in via ``show_imports``
    and ``show_nested_types``. ``hide`` omits matching definition/row IDs or
    KSY source filenames and their connections before layout. The input graph
    is not modified. ``color_type_edges`` assigns a
    distinct color to each type reference, cycling after twelve connections.
    ``router`` selects a registered routing algorithm or a custom router class.
    ``layout`` selects node placement, which runs before edge routing.
    ``layout_width`` sets the vertical layout's width budget or an optional
    hard canvas-width cap for ``optimized-with-space`` (automatic if omitted).
    ``treeshake`` omits unused definitions from imported schemas.
    """
    graph = filter_hidden(graph, hide)
    if treeshake:
        graph = filter_unused_imports(graph)
    hidden_kinds = set()
    if not show_imports:
        hidden_kinds.add("import")
    if not show_nested_types:
        hidden_kinds.add("contains")
    # Filter before layout so hidden connections consume no ports or routes.
    graph = Graph(graph.title, graph.nodes,
                  [edge for edge in graph.edges if edge.kind not in hidden_kinds],
                  graph.root_source_file)
    engine_type = get_layout_engine(layout)
    web_vertical = engine_type.web_vertical
    layout = layout_graph(graph, router=router, layout=layout, layout_width=layout_width)
    color_indices: dict[int, int] = {}
    if color_type_edges:
        # Semantic order keeps colors independent of routing and other edge
        # kinds, including when import/nesting visibility changes.
        type_edges = sorted(
            ((index, route.edge) for index, route in enumerate(layout.edges) if route.edge.kind == "type"),
            key=lambda item: (item[1].source, item[1].source_row or "", item[1].target, item[1].label),
        )
        color_indices = {index: rank % len(TYPE_EDGE_COLORS)
                         for rank, (index, _) in enumerate(type_edges)}
    palette = TYPE_EDGE_COLORS[:min(len(color_indices), len(TYPE_EDGE_COLORS))]
    # Crop blank space without moving the routing coordinates. Preserve any
    # connector tracks that extend into the usual left margin.
    left = min(32, max(0, min([64, *(node.x for node in layout.nodes),
                              *(group.x for group in layout.groups),
                              *(x for edge in layout.edges for x, _ in edge.points)]) - 8))
    width, height = _number(layout.width - left), _number(layout.height)
    title = graph.title or "Kaitai Struct schema"
    visible_title = title.replace("\n", " ")
    max_title_units = min(90, int((layout.width - 128) / (25 * 0.67)))
    if text_width(visible_title) > max_title_units:
        while text_width(visible_title) > max_title_units - 1:
            visible_title = visible_title[:-1]
        visible_title += "…"
    # A standalone SVG root with tabindex="0" traps initial Tab navigation in
    # Firefox. Keep it focusable for canvas shortcuts, outside the Tab sequence.
    import_description = " Orange dashed open arrows show imports." if show_imports else ""
    nested_description = (" Teal solid lines with a hollow diamond at the parent show nested definitions."
                          if show_nested_types else "")
    color_description = (" Type connections use up to 12 distinct colors, reused after 12 connections."
                         if color_type_edges else "")
    description = "Kaitai Struct schema."
    caption = "pos: byte.bit · B/bit"
    root_attrs = f'width="{width}" height="{height}"'
    css = _CSS
    if interactive:
        description = ("Interactive Kaitai Struct schema. Hover or focus a type or field to read its documentation. "
                       "Scroll to zoom, drag to pan, and press 0 to reset.")
        caption += " · Hover/Tab: docs · Wheel: zoom · Drag: pan"
        root_attrs = 'xmlns:xhtml="http://www.w3.org/1999/xhtml" width="100%" height="100%" tabindex="-1"'
        css += _INTERACTIVE_CSS
    if web_vertical:
        # Keep an intrinsic aspect ratio so embedding in a narrow web page
        # shrinks the width and gives the full diagram its natural height.
        root_attrs = f'width="{width}" height="{height}" style="max-width:100%;height:auto;display:block"'
        if interactive:
            root_attrs += ' xmlns:xhtml="http://www.w3.org/1999/xhtml" tabindex="-1"'
            css += '\nsvg { touch-action: pan-y pinch-zoom; }\n'
            description = description.replace("Scroll to zoom", "Scroll to read, Ctrl/Command+scroll to zoom")
            caption = caption.replace("Wheel: zoom", "Ctrl/Command+Wheel: zoom")
    for index, color in enumerate(palette):
        css += (f'\n.type-edge-color-{index} .edge-path {{ stroke: {color}; }}'
                f'\n.type-edge-color-{index} .port {{ fill: {color}; }}\n')
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" {root_attrs} viewBox="{_number(left)} 0 {width} {height}" role="graphics-document" aria-labelledby="diagram-title diagram-desc">',
             f'<title id="diagram-title">{_xml(title)}</title>',
             f'<desc id="diagram-desc">{description} Arrows connect a field to the type or enum it uses.{import_description}{nested_description}{color_description}</desc>',
             '<defs>', f'<style><![CDATA[{css}]]></style>',
             '<filter id="card-shadow" x="-10%" y="-10%" width="120%" height="125%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#233e5b" flood-opacity=".07"/></filter>']
    arrows = [("arrow", "#6593af"), ("arrow-enum", "#9a81bc")]
    arrows.extend((f"arrow-type-{index}", color) for index, color in enumerate(palette))
    for name, color in arrows:
        parts.append(f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1 L9 5 L1 9 Z" fill="{color}"/></marker>')
    parts.extend([
        '<marker id="arrow-import" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto"><path d="M2 2 L10 6 L2 10" fill="none" stroke="#b66a16" stroke-width="1.8" stroke-linejoin="round"/></marker>',
        '<marker id="diamond-contains" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="12" markerHeight="12" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M1 6 L6 1 L11 6 L6 11 Z" fill="#f4f7fb" stroke="#177d72" stroke-width="1.5" stroke-linejoin="round"/></marker>',
    ])
    type_legend = '<path d="M64 106 H88" fill="none" stroke="#6593af" stroke-width="1.8"/><text x="94" y="110">Type</text>'
    if color_type_edges:
        swatches = "".join(f'<path d="M{64 + index * 8} 106 h8" fill="none" stroke="{color}" stroke-width="2.4"/>'
                           for index, color in enumerate(TYPE_EDGE_COLORS[:3]))
        type_legend = (f'<g><title>Type connections: up to 12 distinct colors; colors repeat after 12.</title>'
                       f'{swatches}<text x="94" y="110">Type</text></g>')
    parts.extend(['</defs>', f'<rect class="canvas-bg" x="{_number(left)}" width="{width}" height="{height}"/>', '<g id="viewport">',
                  f'<text class="diagram-title" x="64" y="43">{_xml(visible_title)}</text>',
                  f'<text class="diagram-caption" x="64" y="65">{len(graph.nodes)} definitions · {len(graph.edges)} connections</text>',
                  f'<text class="diagram-caption" x="64" y="85">{caption}</text>',
                  '<g class="legend" aria-label="Connection types">',
                  type_legend,
                  '<path d="M141 106 H165" fill="none" stroke="#9a81bc" stroke-width="1.8"/><text x="171" y="110">Enum</text>'])
    if show_imports:
        parts.append('<g class="edge-kind-import"><path class="legend-path" d="M224 106 H264" marker-end="url(#arrow-import)"/><text x="273" y="110">Import</text></g>')
    if show_nested_types:
        nested_x = 338 if show_imports else 224
        parts.append(f'<g class="edge-kind-contains"><path class="legend-path" d="M{nested_x} 106 H{nested_x + 40}" marker-start="url(#diamond-contains)"/><text x="{nested_x + 48}" y="110">Nested type</text></g>')
    parts.append('</g>')
    for group in layout.groups:
        parts.extend([
            f'<g class="file-group" data-source-file="{_attr(group.source_file)}" role="group" aria-label="{_attr(group.title)}" transform="translate({_number(group.x)} {_number(group.y)})">',
            f'<rect class="file-group-box" width="{_number(group.width)}" height="{_number(group.height)}" rx="14"/>',
            _text_lines(group.title_lines, 24, 26, 19, "file-group-title"),
            f'<text class="file-group-count" x="{_number(group.width - 24)}" y="26" text-anchor="end">{len(group.node_ids)} {"definition" if len(group.node_ids) == 1 else "definitions"}</text>',
            '</g>',
        ])
    parts.append('<g class="edges">')
    titles = {node.node.id: node.node.title for node in layout.nodes}
    label_context = _EdgeLabelContext(layout, left) if engine_type.route_clearance > 0 else None
    parts.extend(_render_edge(edge, titles, interactive=interactive, color_index=color_indices.get(index),
                             label_context=label_context)
                 for index, edge in enumerate(layout.edges))
    parts.append('</g><g class="nodes">')
    # Keep the DOM hierarchy consistent with the visual file boundaries.
    grouped: dict[str, list[tuple[int, NodeLayout]]] = {}
    for index, node in enumerate(layout.nodes):
        grouped.setdefault(node.node.source_file, []).append((index, node))
    for source_file, members in grouped.items():
        parts.append(f'<g class="file-nodes" data-source-file="{_attr(source_file)}">')
        parts.extend(_render_node(node, index, interactive=interactive) for index, node in members)
        parts.append('</g>')
    if not layout.nodes:
        parts.append('<text class="empty-label" x="64" y="140">This schema has no definitions.</text>')
    parts.append('</g></g>')
    if interactive:
        parts.append('<g id="controls" class="controls" role="group" aria-label="Diagram controls">')
        for action, x, button_width, label, glyph in (("out", 0, 36, "Zoom out", "−"), ("in", 42, 36, "Zoom in", "+"), ("reset", 84, 54, "Reset view (0)", "Reset")):
            text_class = ' class="reset-text"' if action == "reset" else ""
            parts.append(f'<g class="control" data-action="{action}" transform="translate({x} 0)" tabindex="0" role="button" aria-label="{label}"><title>{label}</title><rect width="{button_width}" height="34" rx="7"/><text{text_class} x="{button_width / 2:g}" y="17">{glyph}</text></g>')
        tooltip_box = ('<div xmlns="http://www.w3.org/1999/xhtml" class="tooltip-box"></div>'
                       if web_vertical else '<xhtml:div class="tooltip-box"/>')
        parts.extend(['</g>', f'<g id="tooltip" class="tooltip-layer" role="tooltip"><foreignObject width="440" height="300">{tooltip_box}</foreignObject></g>',
                      f'<script><![CDATA[{_vertical_interaction_script() if web_vertical else _SCRIPT}]]></script>'])
    parts.append('</svg>')
    return "\n".join(parts) + "\n"
