"""Deterministic layered layout and obstacle-avoiding orthogonal connectors.

The layout needs no external executable.  Node rectangles are hard obstacles to
the router; only the short connections between a port and its routing anchor
enter the clearance area around their own node.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import heapq
import math
import unicodedata

from .model import Edge, Graph, Node, Row
from .routing import PORT_OFFSET, Router, get_router
from .placement import LayoutEngine, get_layout_engine
from .geometry import measure_paths


@dataclass
class RowLayout:
    row: Row
    y: float
    height: float
    label_lines: list[str]
    type_lines: list[str]
    detail_lines: list[str]
    pos_lines: list[str]
    size_lines: list[str]
    detail_y: float


@dataclass
class TableColumn:
    key: str
    label: str
    x: float
    width: float


@dataclass
class SectionLayout:
    kind: str
    label: str
    y: float
    height: float
    header_height: float
    table_header_height: float
    rows: list[RowLayout]


@dataclass
class NodeLayout:
    node: Node
    x: float
    y: float
    width: float
    height: float
    header_height: float
    rows: list[RowLayout]
    title_lines: list[str]
    subtitle_lines: list[str]
    size_lines: list[str]
    columns: list[TableColumn]
    sections: list[SectionLayout]


@dataclass
class EdgeLayout:
    edge: Edge
    points: list[tuple[float, float]]


@dataclass
class FileGroupLayout:
    source_file: str
    title: str
    x: float
    y: float
    width: float
    height: float
    node_ids: list[str]
    title_lines: list[str]
    header_height: float


@dataclass
class Layout:
    nodes: list[NodeLayout]
    edges: list[EdgeLayout]
    width: float
    height: float
    groups: list[FileGroupLayout] = field(default_factory=list)


def text_width(text: str) -> float:
    """Conservative sans-serif advance in units of 0.67em.

    Broad Latin glyphs need more room than an average character. CJK and
    unfamiliar Unicode retain extra fallback-font clearance, without requiring
    a font installation or browser during conversion.
    """
    total = 0.0
    for char in text:
        if unicodedata.combining(char):
            continue
        if unicodedata.east_asian_width(char) in "WF":
            total += 2
        elif char in "MWmw@%&" or not char.isascii():
            total += 1.6
        elif char in "ijlI.,:;'!| ()[]{}":
            total += 0.8
        elif char.isupper() or char in "+<=>^~":
            total += 1.3
        else:
            total += 1
    return total


def wrap_text(text: str, limit: int) -> list[str]:
    """Wrap text without losing characters, including long type expressions."""
    return _wrap_text(text, limit, text_width)


def monospace_width(text: str) -> float:
    """Conservative monospace advance in 0.67em units, with CJK fallback room."""
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in "WF" or not char.isascii() else 1
               for char in text)


def wrap_monospace(text: str, limit: int) -> list[str]:
    """Wrap identifiers and expressions without assuming narrow Latin glyphs."""
    return _wrap_text(text, limit, monospace_width)


def _wrap_text(text: str, limit: int, measure) -> list[str]:
    result: list[str] = []
    for paragraph in str(text).expandtabs(4).splitlines():
        current = ""
        width = 0
        for char in paragraph:
            size = measure(char)
            if current and width + size > limit:
                # Prefer a nearby word boundary, but keep arbitrary identifiers.
                boundary = current.rfind(" ")
                if boundary >= len(current) // 2:
                    result.append(current[:boundary])
                    current = current[boundary + 1 :] + char
                    width = measure(current)
                else:
                    result.append(current)
                    current = char
                    width = size
            else:
                current += char
                width += size
        result.append(current)
    return result


def _measure(node: Node) -> NodeLayout:
    # Cell widths adapt to the schema but stay bounded: large expressions wrap
    # instead of making a single type disproportionately wide.
    values = {
        "pos": [getattr(row, "pos_label", "—") for row in node.rows],
        "size": [getattr(row, "size_label", "—") for row in node.rows],
        "type": [row.type_label for row in node.rows],
        "id": [row.label or row.id for row in node.rows],
    }
    enum = node.kind == "enum"
    bounds = ({"type": (72, 112), "id": (200, 328)} if enum else
              {"pos": (64, 96), "size": (70, 100), "type": (150, 236), "id": (170, 288)})
    widths = {}
    for key, (minimum, maximum) in bounds.items():
        font_size = 13 if key == "id" else 12
        desired = max((monospace_width(value) for value in values[key]), default=0) * font_size * .67 + 26
        widths[key] = min(maximum, max(minimum, math.ceil(desired)))
    width = max(320 if enum else 560, min(440 if enum else 720, sum(widths.values())))
    # Preserve the compact numeric columns when distributing spare space.
    difference = width - sum(widths.values())
    widths["type"] += difference / 2
    widths["id"] += difference / 2
    columns = []
    x = 0.0
    for key in bounds:
        columns.append(TableColumn(key, "value" if enum and key == "type" else key, x, widths[key]))
        x += widths[key]
    display_title = node.title or node.id
    if getattr(node, "source_file", "") and "::" in display_title:
        display_title = display_title.split("::", 1)[1]
    title_width = width - (32 if enum else 156)
    title = wrap_monospace(display_title, int(title_width / (17 * .67))) or ["Unnamed type"]
    subtitle = wrap_text(node.subtitle, int(title_width / (11 * .67)))
    size_lines = [] if enum else wrap_monospace(getattr(node, "size_label", "—"), 12) or ["—"]
    header_height = max(20 + 20 * len(title) + 14 * len(subtitle), 42 + 15 * len(size_lines) if size_lines else 0)
    rows: list[RowLayout] = []
    sections: list[SectionLayout] = []
    cursor = header_height
    grouped: dict[str, list[Row]] = defaultdict(list)
    section_names = {"param": ("params", "params"), "field": ("seq", "seq"), "instance": ("instances", "instances"), "enum": ("values", "values")}
    for row in node.rows:
        grouped[row.kind].append(row)
    for kind in [*section_names, *(key for key in grouped if key not in section_names)]:
        if not grouped.get(kind):
            continue
        section_kind, label = section_names.get(kind, (kind, kind))
        section = SectionLayout(section_kind, label, cursor, 46, 24, 22, [])
        cursor += section.height
        for row in grouped[kind]:
            labels = wrap_monospace(row.label or row.id, int((widths["id"] - 26) / (13 * .67))) or ["Field"]
            types = wrap_monospace(row.type_label, int((widths["type"] - (26 if enum else 20)) / (12 * .67)))
            positions = [] if enum else wrap_monospace(getattr(row, "pos_label", "—"), int((widths["pos"] - 26) / (12 * .67)))
            sizes = [] if enum else wrap_monospace(getattr(row, "size_label", "—"), int((widths["size"] - 20) / (12 * .67)))
            details = wrap_monospace(row.details, int((width - 32) / (11 * .67)))
            cell_lines = max(len(labels), len(types), len(positions), len(sizes))
            detail_y = 18 + 16 * cell_lines
            height = 10 + 16 * cell_lines + 13 * len(details)
            measured = RowLayout(row, cursor, height, labels, types, details, positions, sizes, detail_y)
            rows.append(measured)
            section.rows.append(measured)
            section.height += height
            cursor += height
        sections.append(section)
    if not rows:
        cursor += 24
    return NodeLayout(node, 0, 0, width, cursor, header_height, rows, title, subtitle, size_lines, columns, sections)


def _ranks(graph: Graph) -> dict[str, int]:
    """Condense strongly connected components before assigning DAG ranks."""
    ids = [node.id for node in graph.nodes]
    adjacency: dict[str, list[str]] = {key: [] for key in ids}
    reverse: dict[str, list[str]] = {key: [] for key in ids}
    for edge in graph.edges:
        if edge.source in adjacency and edge.target in adjacency:
            adjacency[edge.source].append(edge.target)
            reverse[edge.target].append(edge.source)
    visited: set[str] = set()
    finished: list[str] = []
    # Iterative DFS also handles schemas containing thousands of types.
    for start in ids:
        if start in visited:
            continue
        visited.add(start)
        stack = [(start, iter(adjacency[start]))]
        while stack:
            key, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(key)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(adjacency[child])))
    component: dict[str, int] = {}
    component_count = 0
    for start in reversed(finished):
        if start in component:
            continue
        component[start] = component_count
        pending = [start]
        while pending:
            for child in reverse[pending.pop()]:
                if child not in component:
                    component[child] = component_count
                    pending.append(child)
        component_count += 1
    dag: dict[int, set[int]] = defaultdict(set)
    indegree = [0] * component_count
    for edge in graph.edges:
        if edge.source not in component or edge.target not in component:
            continue
        source, target = component[edge.source], component[edge.target]
        if source != target and target not in dag[source]:
            dag[source].add(target)
            indegree[target] += 1
    pending = [key for key, degree in enumerate(indegree) if not degree]
    heapq.heapify(pending)
    ranks = [0] * component_count
    while pending:
        source = heapq.heappop(pending)
        for target in sorted(dag[source]):
            ranks[target] = max(ranks[target], ranks[source] + 1)
            indegree[target] -= 1
            if not indegree[target]:
                heapq.heappush(pending, target)
    return {key: ranks[component[key]] for key in ids}


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if result and result[-1] == point:
            continue
        while len(result) > 1 and (
            result[-2][0] == result[-1][0] == point[0]
            or result[-2][1] == result[-1][1] == point[1]
        ):
            result.pop()
        result.append(point)
    return result


def _place_layered(graph: Graph, nodes: list[NodeLayout]) -> None:
    """Keep the general graph layout for callers without source-file metadata."""
    ranks = _ranks(graph)
    columns: dict[int, list[NodeLayout]] = defaultdict(list)
    for node in nodes:
        columns[ranks[node.node.id]].append(node)
    neighbors: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        neighbors[edge.source].append(edge.target)
        neighbors[edge.target].append(edge.source)
    for iteration in range(4):
        positions = {node.node.id: index for column in columns.values() for index, node in enumerate(column)}
        for rank in sorted(columns, reverse=bool(iteration % 2)):
            def barycenter(node: NodeLayout) -> float:
                others = [positions[key] for key in neighbors[node.node.id] if ranks[key] != rank]
                return sum(others) / len(others) if others else positions[node.node.id]
            columns[rank].sort(key=barycenter)
    widths = {rank: max(node.width for node in column) for rank, column in columns.items()}
    column_x = {0: 64.0}
    for rank in range(1, max(columns) + 1):
        count = sum(min(ranks[e.source], ranks[e.target]) < rank <= max(ranks[e.source], ranks[e.target]) for e in graph.edges)
        column_x[rank] = column_x[rank - 1] + widths.get(rank - 1, 560) + max(140, 36 + 22 * max(0, count - 1))
    heights = {rank: sum(node.height for node in column) + 48 * (len(column) - 1) for rank, column in columns.items()}
    max_height = max(heights.values())
    for rank, column in columns.items():
        y = 132 + (max_height - heights[rank]) / 2
        for node in column:
            node.x, node.y = column_x[rank], y
            y += node.height + 48


def _place_file_groups(nodes: list[NodeLayout]) -> list[FileGroupLayout]:
    """Pack complete file panels, with balanced columns inside larger files.

    Imported enums often contain many more rows than the entry type. A bounded
    two/three-column layout keeps those files readable without a single tall
    tower, while a second packing step keeps each source file visibly separate.
    """
    buckets: dict[str, list[NodeLayout]] = defaultdict(list)
    for node in nodes:
        buckets[getattr(node.node, "source_file", "")].append(node)
    panels: list[FileGroupLayout] = []
    members: dict[str, list[NodeLayout]] = {}
    for source_file, group_nodes in buckets.items():
        column_count = min(len(group_nodes), 3 if len(group_nodes) >= 7 else 2)
        columns: list[list[NodeLayout]] = [[] for _ in range(column_count)]
        heights = [0.0] * column_count
        # Keep the file root first; place the remaining largest cards first to
        # balance enum-heavy panels rather than preserving an arbitrary tower.
        ordered = [group_nodes[0], *sorted(group_nodes[1:], key=lambda node: -node.height)]
        for node in ordered:
            column = min(range(column_count), key=lambda index: (heights[index], index))
            columns[column].append(node)
            heights[column] += node.height + 48
        widths = [max(node.width for node in column) for column in columns]
        x = 24.0
        for column, width in zip(columns, widths):
            y = 44.0
            for node in column:
                node.x, node.y = x, y
                y += node.height + 48
            # Keep enough parallel routing lanes even in compact cards. The
            # visual whitespace savings belong inside cards and between rows;
            # squeezing this gutter merges high-fanout connector paths.
            x += width + 180
        panel_width = x - 180 + 24
        title = source_file or "Types"
        title_lines = wrap_text(title, int((panel_width - 190) / (15 * .67)))
        header_height = 44 + 19 * max(0, len(title_lines) - 1)
        for node in group_nodes:
            node.y += header_height - 44
        panel = FileGroupLayout(source_file, title, 0, 0, panel_width,
                                max(heights) - 48 + header_height + 24,
                                [node.node.id for node in group_nodes], title_lines, header_height)
        panels.append(panel)
        members[source_file] = group_nodes
    # Compare stacked panels against two balanced lanes using their actual
    # fit in a 3:2 viewport. Wide imported files often fit better stacked;
    # several small imports alongside one tall enum file benefit from two lanes.
    candidates = []
    for lane_count in range(1, min(2, len(panels)) + 1):
        candidate_lanes: list[list[FileGroupLayout]] = [[] for _ in range(lane_count)]
        heights = [0.0] * lane_count
        for panel in panels:
            lane = min(range(lane_count), key=lambda index: (heights[index], index))
            candidate_lanes[lane].append(panel)
            heights[lane] += panel.height + 96
        candidate_widths = [max(panel.width for panel in lane) for lane in candidate_lanes]
        canvas_width = 152 + sum(candidate_widths) + 160 * (lane_count - 1)
        canvas_height = 220 + max(heights) - 96
        score = (max(canvas_width / 1.5, canvas_height), canvas_width * canvas_height, lane_count)
        candidates.append((score, candidate_lanes, candidate_widths))
    _, lanes, widths = min(candidates, key=lambda candidate: candidate[0])
    x = 64.0
    for lane, width in zip(lanes, widths):
        y = 132.0
        for panel in lane:
            panel.x, panel.y = x, y
            for node in members[panel.source_file]:
                node.x += panel.x
                node.y += panel.y
            y += panel.height + 96
        x += width + 160
    return [panel for panel in panels if panel.source_file]


def _route_edges(graph: Graph, nodes: list[NodeLayout], groups: list[FileGroupLayout],
                 router_type: type[Router], outgoing, incoming) -> list[EdgeLayout]:
    """Assign actual row/header ports again whenever node positions change."""
    by_id = {node.node.id: node for node in nodes}
    source_ports: dict[int, tuple[float, float]] = {}
    for (node_id, row_id), indices in outgoing.items():
        node = by_id[node_id]
        center = node.y + node.header_height / 2
        spacing = 12
        if row_id is not None:
            row = next(row for row in node.rows if row.row.id == row_id)
            center = node.y + row.y + row.height / 2
            spacing = 18
        indices.sort(key=lambda index: (by_id[graph.edges[index].target].y, index))
        for order, index in enumerate(indices):
            source_ports[index] = (node.x + node.width, center + spacing * (order - (len(indices) - 1) / 2))
    target_ports: dict[int, tuple[float, float]] = {}
    for node_id, indices in incoming.items():
        node = by_id[node_id]
        indices.sort(key=lambda index: (source_ports[index][1], index))
        for order, index in enumerate(indices):
            target_ports[index] = (node.x, node.y + node.header_height / 2 + 12 * (order - (len(indices) - 1) / 2))
    ports = [(source_ports[index], target_ports[index]) for index in range(len(graph.edges))]
    connections = [((start[0] + PORT_OFFSET, start[1]), (end[0] - PORT_OFFSET, end[1]))
                   for start, end in ports]
    anchors = [anchor for connection in connections for anchor in connection]
    routing = router_type(nodes, anchors, groups)
    paths = routing.route_all(connections)
    if len(paths) != len(graph.edges):
        raise ValueError("Router must return one path per connector")
    edges = [EdgeLayout(edge, _simplify([start, *path, end]))
             for edge, (start, end), path in zip(graph.edges, ports, paths)]
    return edges


def layout_graph(graph: Graph, *, router: str | type[Router] = "default",
                 layout: str | type[LayoutEngine] = "default") -> Layout:
    """Measure, place, assign ports, route, and select a complete drawing.

    The optimized engine offers a bounded shortlist, including the original
    placement. Each candidate receives fresh ports and a fresh selected router.
    Shared length, crossings, bends, route length and area decide the winner.
    """
    router_type = get_router(router)
    engine_type = get_layout_engine(layout)
    if not graph.nodes:
        return Layout([], [], 720, 260)
    ids = [node.id for node in graph.nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("Graph contains duplicate node IDs")
    for node in graph.nodes:
        if len({row.id for row in node.rows}) != len(node.rows):
            raise ValueError(f"Node {node.id!r} contains duplicate row IDs")
    valid = set(ids)
    for edge in graph.edges:
        if edge.source not in valid or edge.target not in valid:
            raise ValueError(f"Connector references an unknown node: {edge.source!r} → {edge.target!r}")
    nodes = [_measure(node) for node in graph.nodes]
    by_id = {node.node.id: node for node in nodes}
    outgoing: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    incoming: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(graph.edges):
        outgoing[(edge.source, edge.source_row)].append(index)
        incoming[edge.target].append(index)
        if edge.source_row is not None and not any(row.row.id == edge.source_row for row in by_id[edge.source].rows):
            raise ValueError(f"Connector references unknown row {edge.source_row!r} in {edge.source!r}")
    # Ports occupy separate positions within their actual header or field.
    # Expand unusually busy headers/fields instead of squeezing arrows together.
    for node in nodes:
        count = max(len(outgoing[(node.node.id, None)]), len(incoming[node.node.id]))
        extra = max(0, 24 + 12 * (count - 1) - node.header_height)
        node.header_height += extra
        offset = extra
        for section in node.sections:
            section.y += offset
            for row in section.rows:
                row.y += offset
                count = len(outgoing[(node.node.id, row.row.id)])
                extra = max(0, 24 + 18 * (count - 1) - row.height)
                row.height += extra
                section.height += extra
                offset += extra
        node.height += offset
    placements = engine_type().placements(graph, nodes)
    if not placements:
        raise ValueError("Layout engine must return at least one placement")
    best = None
    routing_error = None
    for placement in placements:
        if len(placement.positions) != len(nodes):
            raise ValueError("Layout engine must return one position per node")
        for node, (x, y) in zip(nodes, placement.positions):
            if not (math.isfinite(x) and math.isfinite(y) and x >= 0 and y >= 114):
                raise ValueError("Layout positions must be finite and below the diagram caption")
            node.x, node.y = x, y
        groups = placement.groups
        try:
            edges = _route_edges(graph, nodes, groups, router_type, outgoing, incoming)
        except ValueError as error:
            routing_error = error
            continue
        width = max([node.x + node.width for node in nodes] + [group.x + group.width for group in groups]) + 88
        height = max([node.y + node.height for node in nodes] + [group.y + group.height for group in groups]) + 88
        width = max(680, width)
        # The single-candidate default retains its original routes and cost.
        score = ((*measure_paths([edge.points for edge in edges]).quality, width * height)
                 if len(placements) > 1 else ())
        if best is None or score < best[0]:
            best = score, placement, edges, width, height
    if best is None:
        raise routing_error or ValueError("Could not route any layout candidate")
    _, placement, edges, width, height = best
    for node, position in zip(nodes, placement.positions):
        node.x, node.y = position
    return Layout(nodes, edges, width, height, placement.groups)
