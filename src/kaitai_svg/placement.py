"""Interchangeable node placement, independent of connector routing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from collections.abc import Sequence
import math
from statistics import median
from typing import TYPE_CHECKING

from .model import Edge, Graph, Node

if TYPE_CHECKING:
    from .geometry import PathMetrics
    from .layout import FileGroupLayout, NodeLayout


@dataclass
class Placement:
    """One complete candidate; positions follow the input node order."""

    positions: list[tuple[float, float]]
    groups: list[FileGroupLayout]


class LayoutEngine(ABC):
    """Produce deterministic candidate positions for measured node rectangles.

    Preserve node dimensions and semantic row order. Candidates must have one
    position per node, disjoint cards and panels, and enough header/port space.
    Layout orchestration recomputes ports and routes each returned candidate,
    then retains the best complete drawing. Keep candidate lists bounded.
    """

    web_vertical = False

    def set_width_limit(self, width: float) -> None:
        raise ValueError("layout_width is supported only by the vertical-optimized layout")

    def empty_size(self) -> tuple[float, float]:
        return 720, 260

    def accepts_canvas(self, width: float, height: float) -> bool:
        return True

    def score(self, metrics: PathMetrics, width: float, height: float) -> tuple:
        return (*metrics.quality, width * height)

    @abstractmethod
    def placements(self, graph: Graph, nodes: list[NodeLayout]) -> Sequence[Placement]:
        raise NotImplementedError


class DefaultLayout(LayoutEngine):
    """The original height-balanced file panels or ungrouped layered layout."""

    def placements(self, graph: Graph, nodes: list[NodeLayout]) -> list[Placement]:
        from .layout import _place_file_groups, _place_layered

        if any(node.node.source_file for node in nodes):
            groups = _place_file_groups(nodes)
        else:
            _place_layered(graph, nodes)
            groups = []
        return [Placement([(node.x, node.y) for node in nodes], groups)]


class OptimizedLayout(DefaultLayout):
    """Graph-aware placement with a bounded shortlist for actual routing.

    Start with the original packing and SCC-based dependency columns. Alternate
    port-aware ordering sweeps, try local card/column and panel-order changes,
    and cheaply score them before returning at most four complete placements.
    Actual routing, port reassignment and final selection happen in layout.py.
    """

    max_routed_candidates = 4
    max_local_candidates = 64
    column_spacing = 180

    def placements(self, graph: Graph, nodes: list[NodeLayout]) -> list[Placement]:
        from .layout import _ranks

        baseline = super().placements(graph, nodes)[0]
        if not graph.edges or len(nodes) < 2:
            return [baseline]
        indices = {node.node.id: i for i, node in enumerate(nodes)}
        edges = [(indices[edge.source], indices[edge.target], edge.source_row) for edge in graph.edges]
        buckets: dict[str, list[int]] = defaultdict(list)
        for i, node in enumerate(nodes):
            buckets[node.node.source_file].append(i)
        self._internal_edges = defaultdict(list)
        for a, b, row in edges:
            if nodes[a].node.source_file == nodes[b].node.source_file:
                self._internal_edges[nodes[a].node.source_file].append((a, b, row))
        # File quotient graph guides whole-panel ordering, including references
        # to imported files. Keep the input file first when metadata identifies it.
        file_edges = [Edge(nodes[a].node.source_file, nodes[b].node.source_file)
                      for a, b, _ in edges if nodes[a].node.source_file != nodes[b].node.source_file]
        file_ranks = _ranks(Graph("Files", [Node(name, name) for name in buckets], file_edges))
        file_order = {name: i for i, name in enumerate(buckets)}
        files = sorted(buckets, key=lambda name: (
            name != graph.root_source_file if graph.root_source_file else False,
            file_ranks[name], file_order[name]))

        # A panel is (source file, columns of indices into the measured nodes).
        original = []
        layered = []
        for name in files:
            members = buckets[name]
            member_ids = {nodes[i].node.id for i in members}
            columns: dict[float, list[int]] = defaultdict(list)
            for i in members:
                columns[baseline.positions[i][0]].append(i)
            original.append((name, [sorted(column, key=lambda i: baseline.positions[i][1])
                                    for _, column in sorted(columns.items())]))
            subgraph = Graph(name, [nodes[i].node for i in members],
                             [edge for edge in graph.edges
                              if edge.source in member_ids and edge.target in member_ids])
            ranks = _ranks(subgraph)
            maximum = max(ranks.values(), default=0)
            count = min(len(members), max(2, min(4, maximum + 1)))
            ranked: list[list[int]] = [[] for _ in range(count)]
            for order, i in enumerate(members):
                column = (round(ranks[nodes[i].node.id] * (count - 1) / maximum) if maximum else
                          min(count - 1, order * count // len(members)))
                ranked[column].append(i)
            layered.append((name, [column for column in ranked if column]))

        pool: dict[tuple, tuple[float, Placement]] = {}

        def add(placement: Placement) -> None:
            signature = tuple(placement.positions)
            if signature not in pool:
                pool[signature] = self._proxy(placement, nodes, edges), placement

        add(baseline)
        configurations = []
        for panels in (original, layered):
            ordered = self._order(panels, nodes, edges)
            for lanes in range(1, min(2, len(panels)) + 1):
                for align in (False, True):
                    candidate = self._pack(ordered, nodes, edges, lanes, align)
                    add(candidate)
                    configurations.append((self._proxy(candidate, nodes, edges), ordered, lanes, align))
        # Explore neighborhoods of the best two starts. The proxy uses actual
        # row offsets, but only shortlisted candidates pay for obstacle routing.
        budget = self.max_local_candidates
        for _, panels, lanes, align in sorted(configurations, key=lambda item: item[0])[:2]:
            for panel_index, (name, columns) in enumerate(panels):
                for column_index, column in enumerate(columns):
                    for row_index, node_index in enumerate(column):
                        mutations = []
                        if row_index + 1 < len(column):
                            changed = self._copy(panels)
                            destination = changed[panel_index][1][column_index]
                            destination[row_index:row_index + 2] = reversed(destination[row_index:row_index + 2])
                            mutations.append(changed)
                        if len(column) > 1:
                            for other in range(len(columns)):
                                if other == column_index:
                                    continue
                                changed = self._copy(panels)
                                changed[panel_index][1][column_index].remove(node_index)
                                destination = changed[panel_index][1][other]
                                destination.insert(min(row_index, len(destination)), node_index)
                                mutations.append(changed)
                        for changed in mutations:
                            if budget <= 0:
                                break
                            add(self._pack(changed, nodes, edges, lanes, align))
                            budget -= 1
                        if budget <= 0:
                            break
                    if budget <= 0:
                        break
                if budget <= 0:
                    break
            for i in range(len(panels) - 1):
                if budget <= 0:
                    break
                if i == 0 and panels[0][0] == graph.root_source_file:
                    continue
                changed = self._copy(panels)
                changed[i], changed[i + 1] = changed[i + 1], changed[i]
                add(self._pack(changed, nodes, edges, lanes, align))
                budget -= 1
        # Retaining the original drawing makes final selection non-regressing
        # under the documented routing objective, even if the proxy misranks it.
        baseline_signature = tuple(baseline.positions)
        finalists = [entry for signature, entry in pool.items() if signature != baseline_signature]
        finalists.sort(key=lambda entry: entry[0])
        return [baseline, *(placement for _, placement in finalists[:self.max_routed_candidates - 1])]

    @staticmethod
    def _copy(panels):
        return [(name, [column[:] for column in columns]) for name, columns in panels]

    @staticmethod
    def _row_offset(node: NodeLayout, row_id: str | None) -> float:
        if row_id is not None:
            row = next(row for row in node.rows if row.row.id == row_id)
            return row.y + row.height / 2
        return node.header_height / 2

    def _order(self, panels, nodes, edges):
        panels = self._copy(panels)
        for _ in range(6):
            for name, columns in panels:
                ys = {}
                membership = {}
                for j, column in enumerate(columns):
                    y = 0.0
                    for i in column:
                        ys[i], membership[i] = y, j
                        y += nodes[i].height + 48
                for j in range(len(columns)):
                    preferences: dict[int, list[float]] = defaultdict(list)
                    for a, b, row in self._internal_edges[name]:
                        if a not in membership or b not in membership or membership[a] == membership[b]:
                            continue
                        if membership[a] == j:
                            preferences[a].append(ys[b] + nodes[b].header_height / 2 - self._row_offset(nodes[a], row))
                        if membership[b] == j:
                            preferences[b].append(ys[a] + self._row_offset(nodes[a], row) - nodes[b].header_height / 2)
                    columns[j].sort(key=lambda i: (median(preferences[i]) if preferences[i] else ys[i], i))
        return panels

    def _pack(self, panels, nodes, edges, lane_count: int, align: bool) -> Placement:
        from .layout import FileGroupLayout, wrap_text

        framed = any(node.node.source_file for node in nodes)
        records = []
        for name, columns in panels:
            widths = [max(nodes[i].width for i in column) for column in columns]
            padding = 24 if framed else 0
            width = sum(widths) + self.column_spacing * (len(columns) - 1) + 2 * padding
            title = name or "Types"
            lines = wrap_text(title, max(1, int((width - 190) / (15 * .67))))
            header = 44 + 19 * max(0, len(lines) - 1) if framed else 0
            local = {}
            membership = {}
            x = float(padding)
            for j, (column, column_width) in enumerate(zip(columns, widths)):
                y = float(header)
                for i in column:
                    local[i], membership[i] = (x, y), j
                    y += nodes[i].height + 48
                x += column_width + self.column_spacing
            if align and len(columns) > 1:
                offsets = [0.0] * len(columns)
                limit = max(y + nodes[i].height for i, (_, y) in local.items())
                for _ in range(3):
                    for j in range(1, len(columns)):
                        deltas = []
                        for a, b, row in self._internal_edges[name]:
                            if a not in membership or b not in membership or membership[a] == membership[b]:
                                continue
                            delta = (local[a][1] + offsets[membership[a]] + self._row_offset(nodes[a], row)
                                     - local[b][1] - offsets[membership[b]] - nodes[b].header_height / 2)
                            if membership[b] == j:
                                deltas.append(delta)
                            elif membership[a] == j:
                                deltas.append(-delta)
                        if deltas:
                            offsets[j] = min(limit, max(-limit, offsets[j] + median(deltas)))
                minimum = min(offsets)
                local = {i: (x, y + offsets[membership[i]] - minimum) for i, (x, y) in local.items()}
            height = max(y + nodes[i].height for i, (_, y) in local.items()) + (24 if framed else 0)
            records.append((name, width, height, header, lines, local))
        lanes = [[] for _ in range(lane_count)]
        heights = [0.0] * lane_count
        for record in records:
            lane = min(range(lane_count), key=lambda j: (heights[j], j))
            lanes[lane].append(record)
            heights[lane] += record[2] + 96
        positions = [(0.0, 0.0)] * len(nodes)
        groups = []
        x = 64.0
        for lane in lanes:
            y = 132.0
            for name, width, height, header, lines, local in lane:
                for i, (nx, ny) in local.items():
                    positions[i] = x + nx, y + ny
                if name:
                    groups.append(FileGroupLayout(name, name, x, y, width, height,
                                                  [nodes[i].node.id for i in local], lines, header))
                y += height + 96
            x += max(record[1] for record in lane) + 160
        return Placement(positions, groups)

    def _proxy(self, placement: Placement, nodes, edges) -> float:
        connections = []
        length = 0.0
        backward = 0
        for a, b, row in edges:
            ax, ay = placement.positions[a]
            bx, by = placement.positions[b]
            start = (ax + nodes[a].width, ay + self._row_offset(nodes[a], row))
            end = (bx, by + nodes[b].header_height / 2)
            length += abs(start[0] - end[0]) + abs(start[1] - end[1])
            backward += start[0] >= end[0]
            connections.append((start, end))
        # Bound pairwise proxy work on large schemas; exact geometry is measured
        # for every complete path after the shortlisted candidates are routed.
        sample = connections[:128]
        crossings = 0
        def side(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        for i, (a, b) in enumerate(sample):
            for c, d in sample[i + 1:]:
                crossings += side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0
        width = max(x + node.width for node, (x, _) in zip(nodes, placement.positions))
        height = max(y + node.height for node, (_, y) in zip(nodes, placement.positions))
        return 600 * crossings + 400 * backward + length + .15 * math.sqrt(width * height)


class VerticalOptimizedLayout(OptimizedLayout):
    """Fit a web content width, allowing the document to grow vertically.

    File panels occupy one vertical lane. Card columns are admitted only when
    their measured widths, gutters and outer routing space fit the width limit.
    No wide legacy placement is eligible as a fallback. Up to four candidates
    are actually routed; within the width bound, route quality precedes height.
    """

    web_vertical = True
    width_limit = 1200
    column_spacing = 96

    def set_width_limit(self, width: float) -> None:
        try:
            valid = (not isinstance(width, bool) and isinstance(width, (int, float))
                     and math.isfinite(width) and width > 0)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("layout_width must be a finite positive number")
        self.width_limit = width

    def _check_width(self, minimum: float) -> None:
        self.set_width_limit(self.width_limit)
        if self.width_limit < minimum:
            raise ValueError(f"layout_width {self.width_limit:g} is too narrow; "
                             f"at least {minimum:g} pixels are required for readable cards and routing space")

    def empty_size(self) -> tuple[float, float]:
        self._check_width(680)
        return min(720, self.width_limit), 260

    def accepts_canvas(self, width: float, height: float) -> bool:
        return width <= self.width_limit + 1e-8

    def score(self, metrics: PathMetrics, width: float, height: float) -> tuple:
        return (*metrics.quality, height, width)

    def placements(self, graph: Graph, nodes: list[NodeLayout]) -> list[Placement]:
        from .layout import _ranks

        if not nodes:
            self.empty_size()
            return [Placement([], [])]
        framed = any(node.node.source_file for node in nodes)
        # x=64, optional 24px panel padding on each side, and 88px on the
        # right protect ports, outer tracks, arrowheads and the diagram caption.
        margin = 200 if framed else 152
        self._check_width(max(680, max(node.width for node in nodes) + margin))
        indices = {node.node.id: i for i, node in enumerate(nodes)}
        edges = [(indices[edge.source], indices[edge.target], edge.source_row) for edge in graph.edges]
        buckets: dict[str, list[int]] = defaultdict(list)
        self._internal_edges = defaultdict(list)
        for i, node in enumerate(nodes):
            buckets[node.node.source_file].append(i)
        for a, b, row in edges:
            if nodes[a].node.source_file == nodes[b].node.source_file:
                self._internal_edges[nodes[a].node.source_file].append((a, b, row))
        file_edges = [Edge(nodes[a].node.source_file, nodes[b].node.source_file)
                      for a, b, _ in edges if nodes[a].node.source_file != nodes[b].node.source_file]
        file_ranks = _ranks(Graph("Files", [Node(name, name) for name in buckets], file_edges))
        file_order = {name: i for i, name in enumerate(buckets)}
        files = sorted(buckets, key=lambda name: (
            name != graph.root_source_file if graph.root_source_file else False,
            file_ranks[name], file_order[name]))
        ranks = _ranks(graph)
        ordered = {}
        for name in files:
            members = buckets[name]
            ordered[name] = [members[0], *sorted(members[1:], key=lambda i: (ranks[nodes[i].node.id], i))]

        def fits(columns) -> bool:
            width = (sum(max(nodes[i].width for i in column) for column in columns)
                     + self.column_spacing * (len(columns) - 1) + margin)
            return width <= self.width_limit + 1e-8

        def columns_for(members, count, balanced):
            columns = [[] for _ in range(count)]
            heights = [0.0] * count
            for order, i in enumerate(members):
                column = (min(range(count), key=lambda j: (heights[j], j)) if balanced else
                          min(count - 1, order * count // len(members)))
                columns[column].append(i)
                heights[column] += nodes[i].height + 48
            return columns

        baseline_panels = [(name, [ordered[name][:]]) for name in files]
        baseline = self._pack(baseline_panels, nodes, edges, 1, False)
        pool = {tuple(baseline.positions): (self._proxy(baseline, nodes, edges), baseline)}

        def add(panels, align=False):
            if not all(fits(columns) for _, columns in panels):
                return
            candidate = self._pack(panels, nodes, edges, 1, align)
            signature = tuple(candidate.positions)
            if signature not in pool:
                pool[signature] = self._proxy(candidate, nodes, edges), candidate

        configurations = []
        for maximum in range(1, 5):
            for balanced in (False, True):
                panels = []
                for name in files:
                    members = ordered[name]
                    choices = [columns_for(members, count, balanced)
                               for count in range(1, min(maximum, len(members)) + 1)]
                    columns = next(columns for columns in reversed(choices) if fits(columns))
                    panels.append((name, columns))
                panels = self._order(panels, nodes, edges)
                # Keep each file's leading definition at the start of its panel.
                for name, columns in panels:
                    first = buckets[name][0]
                    for column in columns:
                        if first in column:
                            column.remove(first)
                    columns[0].insert(0, first)
                for align in (False, True):
                    add(panels, align)
                    configurations.append((self._proxy(self._pack(panels, nodes, edges, 1, align), nodes, edges),
                                           panels, align))
        # Candidate changes remain confined to the existing width-safe panels.
        budget = self.max_local_candidates
        for _, panels, align in sorted(configurations, key=lambda item: item[0])[:2]:
            for p, (name, columns) in enumerate(panels):
                for c, column in enumerate(columns):
                    for row in range(len(column) - 1):
                        if budget <= 0:
                            break
                        if column[row] == buckets[name][0]:
                            continue
                        changed = self._copy(panels)
                        target = changed[p][1][c]
                        target[row:row + 2] = reversed(target[row:row + 2])
                        add(changed, align)
                        budget -= 1
            for p in range(len(panels) - 1):
                if budget <= 0:
                    break
                if p == 0 and panels[0][0] == graph.root_source_file:
                    continue
                changed = self._copy(panels)
                changed[p], changed[p + 1] = changed[p + 1], changed[p]
                add(changed, align)
                budget -= 1
        alternatives = [entry for key, entry in pool.items() if key != tuple(baseline.positions)]
        alternatives.sort(key=lambda entry: entry[0])
        return [baseline, *(candidate for _, candidate in alternatives[:self.max_routed_candidates - 1])]

    def _proxy(self, placement: Placement, nodes, edges) -> float:
        height = max(y + node.height for node, (_, y) in zip(nodes, placement.positions))
        if not edges:
            return height
        return super()._proxy(placement, nodes, edges) + .02 * height


LAYOUTS: dict[str, type[LayoutEngine]] = {
    "default": DefaultLayout,
    "optimized": OptimizedLayout,
    "vertical-optimized": VerticalOptimizedLayout,
}


def get_layout_engine(layout: str | type[LayoutEngine]) -> type[LayoutEngine]:
    if isinstance(layout, str):
        try:
            return LAYOUTS[layout]
        except KeyError:
            raise ValueError(f"Unknown layout {layout!r}; choose from: {', '.join(LAYOUTS)}") from None
    if isinstance(layout, type) and issubclass(layout, LayoutEngine) and not layout.__abstractmethods__:
        return layout
    raise ValueError("layout must be a built-in name or a concrete LayoutEngine subclass")
