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
    nodes: list[NodeLayout] | None = None


class LayoutEngine(ABC):
    """Produce deterministic candidate positions for measured node rectangles.

    Preserve semantic row order. Candidates must have one
    position per node, disjoint cards and panels, and enough header/port space.
    A candidate may supply independently measured nodes, including port space;
    otherwise the original measured dimensions are preserved.
    Layout orchestration recomputes ports and routes each returned candidate,
    then retains the best complete drawing. Keep candidate lists bounded.
    """

    web_vertical = False
    supports_width_limit = False
    route_clearance = 0.0

    def set_width_limit(self, width: float) -> None:
        raise ValueError("layout_width is supported only by the vertical-optimized and optimized-with-space layouts")

    def empty_size(self) -> tuple[float, float]:
        return 720, 260

    def accepts_canvas(self, width: float, height: float) -> bool:
        return True

    def score(self, metrics: PathMetrics, width: float, height: float) -> tuple:
        return (*metrics.quality, width * height)

    def score_placement(self, metrics: PathMetrics, width: float, height: float,
                        placement: Placement) -> tuple:
        return self.score(metrics, width, height)

    def score_routes(self, metrics: PathMetrics, width: float, height: float,
                     placement: Placement, paths) -> tuple:
        """Score the drawing while preserving existing custom scoring hooks."""
        return self.score_placement(metrics, width, height, placement)

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
    supports_width_limit = True
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
        # 64px placement inset and 32px right margin, plus optional panel padding.
        # Actual outer connector tracks are included after routing.
        margin = 144 if framed else 96
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


class OptimizedWithSpaceLayout(OptimizedLayout):
    """Balance readable reflow, routing and space using bounded candidates.

    Each profile owns fresh card measurements. Boundary traffic determines the
    routing space between cards, columns and file panels. A requested width is
    a hard canvas bound; without one, width and height are selected together.
    """

    supports_width_limit = True
    route_clearance = 8.0
    max_routed_candidates = 6
    max_local_candidates = 64
    width_limit: float | None = None

    def set_width_limit(self, width: float) -> None:
        VerticalOptimizedLayout.set_width_limit(self, width)

    def empty_size(self) -> tuple[float, float]:
        self._check_minimum(680)
        return min(720, self.width_limit) if self.width_limit is not None else 720, 260

    def accepts_canvas(self, width: float, height: float) -> bool:
        return self.width_limit is None or width <= self.width_limit + 1e-8

    def _check_minimum(self, minimum: float) -> None:
        if self.width_limit is not None and self.width_limit < minimum:
            raise ValueError(f"layout_width {self.width_limit:g} is too narrow; "
                             f"at least {minimum:g} pixels are required for readable cards and routing space")

    def score_placement(self, metrics: PathMetrics, width: float, height: float,
                        placement: Placement) -> tuple:
        from .layout import _text_height

        text = _text_height(placement.nodes or [])
        cost = (metrics.crossings / self._edge_count
                + metrics.bends / (2 * self._edge_count)
                + metrics.length / (self._edge_count * self._diagonal)
                + self._space_cost(width, height)
                + text / self._natural_text)
        return (round(metrics.shared_length, 8), cost, metrics.crossings, metrics.bends,
                round(metrics.length, 8), width * height, width, height)

    def score_routes(self, metrics: PathMetrics, width: float, height: float,
                     placement: Placement, paths) -> tuple:
        from .geometry import parallel_conflict_length

        score = self.score_placement(metrics, width, height, placement)
        return (round(metrics.shared_length, 8),
                round(parallel_conflict_length(paths, self.route_clearance), 8), *score)

    def _space_cost(self, width: float, height: float) -> float:
        # An explicit budget reserves a horizontal strip. Compare how much
        # height a drawing occupies in that strip, rather than rewarding a
        # narrow, tall drawing for leaving the supplied width unused.
        reserved = min(self.width_limit, self._useful_width) if self.width_limit is not None else width
        return reserved * height / self._card_area

    def placements(self, graph: Graph, nodes: list[NodeLayout]) -> list[Placement]:
        from .layout import _measure_adaptive, _ranks, _reserve_port_space, _text_height

        if not nodes:
            self.empty_size()
            return [Placement([], [], [])]
        self._card_area = max(1.0, sum(node.width * node.height for node in nodes))
        self._natural_text = max(1.0, _text_height(nodes))
        self._diagonal = max(1.0, median(math.hypot(node.width, node.height) for node in nodes))
        self._edge_count = max(1, len(graph.edges))
        self._useful_width = max(680, sum(node.width for node in nodes) + 360 * len(nodes) + 160)
        framed = any(node.node.source_file for node in nodes)
        margin = 144 if framed else 96
        minima = [272 if node.node.kind == "enum" else 454 for node in nodes]
        self._check_minimum(max(680, max(minima) + margin))
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

        profiles = []
        seen_profiles = {}
        # The last profile keeps a narrow one-column fallback eligible even
        # when compact proportional reflow still exhausts outer routing room.
        for factor in (0.85, 1.0, 1.25, *(() if self.width_limit is None else (0.0,))):
            measured = []
            for node, minimum in zip(nodes, minima):
                maximum = 550 if node.node.kind == "enum" else 900
                target = max(minimum, min(maximum, node.width * factor))
                if self.width_limit is not None:
                    target = min(target, self.width_limit - margin)
                measured.append(_measure_adaptive(node.node, target))
            _reserve_port_space(measured, graph)
            signature = tuple((node.width, node.height,
                               tuple(column.width for column in node.columns)) for node in measured)
            if signature not in seen_profiles:
                seen_profiles[signature] = len(profiles)
                profiles.append((measured, factor == 0.0))
            elif factor == 0.0:
                index = seen_profiles[signature]
                profiles[index] = profiles[index][0], True

        pool = {}
        sequence = 0
        fallback = None
        # Divide the total proxy budget among profiles, rather than multiplying
        # the existing local-search budget by every card-width alternative.
        per_profile = max(1, self.max_local_candidates // len(profiles))
        for profile_index, (measured, narrowest) in enumerate(profiles):
            before = len(pool)
            starts = []

            def add(panels, lanes=1, align=False, *, spacing=1.0, safe=False, local=False):
                nonlocal sequence, fallback
                if len(pool) >= self.max_local_candidates or len(pool) - before >= per_profile:
                    return None
                candidate = self._pack_space(panels, measured, edges, lanes, align, spacing=spacing)
                width, height = self._canvas(candidate, measured)
                if not self.accepts_canvas(width, height):
                    return None
                key = (profile_index, tuple(candidate.positions),
                       tuple((group.x, group.y, group.width, group.height) for group in candidate.groups))
                if key in pool:
                    if safe:
                        fallback = pool[key]
                    return pool[key]
                family = (profile_index, lanes, align,
                          tuple((name, tuple(tuple(column) for column in columns)) for name, columns in panels))
                entry = (self._space_proxy(candidate, measured, edges), sequence,
                         profile_index, candidate, spacing, family)
                sequence += 1
                pool[key] = entry
                if not local:
                    starts.append((entry, panels, lanes, align, spacing))
                if safe:
                    fallback = entry
                return entry

            ordered = {name: [buckets[name][0], *sorted(buckets[name][1:],
                        key=lambda i: (ranks[measured[i].node.id], i))] for name in files}
            single = [(name, [ordered[name][:]]) for name in files]
            add(single, safe=narrowest or self.width_limit is None and profile_index == 0)

            def capacity(members):
                if self.width_limit is None:
                    return min(len(members), max(4, math.ceil(math.sqrt(len(members)))))
                widths = sorted(measured[i].width for i in members)
                used, count = widths[-1] + margin, 1
                # The broadest card must fit one column; the narrowest cards
                # bound the additional columns. Actual packing checks all cards.
                for width in widths[:-1]:
                    if used + width + 72 > self.width_limit + 1e-8:
                        break
                    used += width + 72
                    count += 1
                return count

            capacities = {name: capacity(members) for name, members in ordered.items()}

            def panels_for(count, balanced):
                panels = []
                for name in files:
                    members = ordered[name]
                    columns = [[] for _ in range(min(count, capacities[name]))]
                    heights = [0.0] * len(columns)
                    maximum = max((ranks[measured[i].node.id] for i in members), default=0)
                    minimum = min((ranks[measured[i].node.id] for i in members), default=0)
                    for order, i in enumerate(members):
                        if balanced:
                            column = min(range(len(columns)), key=lambda j: (heights[j], j))
                        elif maximum > minimum:
                            column = round((ranks[measured[i].node.id] - minimum)
                                           * (len(columns) - 1) / (maximum - minimum))
                        else:
                            column = min(len(columns) - 1, order * len(columns) // len(members))
                        columns[column].append(i)
                        heights[column] += measured[i].height + 40
                    columns = [column for column in columns if column]
                    panels.append((name, columns))
                panels = self._order(panels, measured, edges)
                for name, columns in panels:
                    first = buckets[name][0]
                    for column in columns:
                        if first in column:
                            column.remove(first)
                    columns[0].insert(0, first)
                    columns[:] = [column for column in columns if column]
                return panels

            maximum = max(capacities.values())
            counts = (list(range(2, maximum + 1)) if maximum <= 8 else
                      sorted({2, 3, maximum // 3, 2 * maximum // 3, maximum - 1, maximum}))
            # Visit every sampled column count before spending the budget on
            # order/alignment variants of two-column layouts.
            for count in counts:
                panels = panels_for(count, True)
                if add(panels) is None:
                    add(panels, spacing=0.7)
            promising = sorted(starts, key=lambda item: item[0][:2])[:2]
            for _, panels, lanes, align, spacing in promising:
                add(panels, lanes, not align, spacing=spacing)
                count = max(len(columns) for _, columns in panels)
                add(panels_for(count, False), lanes, align, spacing=spacing)
                for alignment in (not align, align):
                    for scale in (0.7, 1.35):
                        add(panels, lanes, alignment, spacing=scale)
            if len(files) > 1:
                if self.width_limit is None:
                    lane_capacity = min(len(files), max(2, math.ceil(math.sqrt(len(files)))))
                else:
                    panel_widths = sorted(max(measured[i].width for i in ordered[name])
                                          + (48 if framed else 0) for name in files)
                    used, lane_capacity = panel_widths[-1] + 96, 1
                    for panel_width in panel_widths[:-1]:
                        if used + panel_width + 96 > self.width_limit + 1e-8:
                            break
                        used += panel_width + 96
                        lane_capacity += 1
                lane_counts = (list(range(2, lane_capacity + 1)) if lane_capacity <= 8 else
                               sorted({2, 3, lane_capacity // 3, 2 * lane_capacity // 3,
                                       lane_capacity - 1, lane_capacity}))
                for lanes in lane_counts:
                    add(single, lanes=lanes)
                for _, panels, _, _, _ in promising:
                    for lanes in lane_counts:
                        add(panels, lanes=lanes)
            # Height balancing alone spreads broad cards over many columns.
            # Reassign the widest cards around the best starts, evaluating
            # actual measured width, height, connectivity and space together.
            for _, panels, lanes, align, spacing in sorted(starts, key=lambda item: item[0][:2])[:2]:
                moves = [(measured[i].width, p, c, r, i)
                         for p, (name, columns) in enumerate(panels)
                         for c, column in enumerate(columns) for r, i in enumerate(column)
                         if i != buckets[name][0]]
                for _, p, c, r, i in sorted(moves, key=lambda item: (-item[0], item[4])):
                    if len(pool) - before >= per_profile:
                        break
                    columns = panels[p][1]
                    others = sorted((j for j in range(len(columns)) if j != c), key=lambda j: (
                        max(0, measured[i].width - max(measured[k].width for k in columns[j])), j))
                    for other in others:
                        if len(pool) - before >= per_profile:
                            break
                        changed = self._copy(panels)
                        changed[p][1][c].remove(i)
                        destination = changed[p][1][other]
                        insertion = min(r, len(destination))
                        destination.insert(max(1, insertion) if other == 0 else insertion, i)
                        changed[p][1][:] = [column for column in changed[p][1] if column]
                        add(changed, lanes, align, spacing=spacing, local=True)

        if not pool:
            raise ValueError("Could not place readable cards within the requested layout_width")
        ranked = sorted(pool.values(), key=lambda entry: entry[:2])
        selected = [fallback] if fallback is not None else []

        def retain(entry):
            if entry is not None and entry not in selected and len(selected) < self.max_routed_candidates:
                selected.append(entry)

        def breadth(entry):
            positions = entry[3].positions
            return max(entry[5][1],
                       max(len({positions[i][0] for i in members}) for members in buckets.values()))

        retain(ranked[0])
        # Evaluate actual routing at a second gap scale for the most promising
        # structure. A distance-only proxy otherwise eliminates wider gutters
        # before the router can reveal their benefit for overlapping paths.
        retain(next((entry for entry in ranked if entry[5] == ranked[0][5]
                     and entry[4] != ranked[0][4]), None))
        if self.width_limit is not None:
            retain(min(ranked, key=lambda entry: (self._canvas(entry[3], entry[3].nodes)[1], entry[:2])))
        column_counts = sorted({breadth(entry) for entry in ranked})
        # Text profiles alone did not provide structural diversity: all six
        # finalists could have two columns despite a large width budget.
        targets = ((column_counts[-1],) if self.width_limit is not None else
                   (column_counts[-1], column_counts[len(column_counts) // 2]))
        for count in targets:
            retain(next(entry for entry in ranked if breadth(entry) == count))
        for profile in range(len(profiles)):
            best = next((entry for entry in ranked if entry[2] == profile), None)
            retain(best)
        for entry in ranked:
            if len(selected) >= self.max_routed_candidates:
                break
            retain(entry)
        return [entry[3] for entry in selected[:self.max_routed_candidates]]

    @staticmethod
    def _canvas(placement: Placement, nodes) -> tuple[float, float]:
        width = max([x + node.width for node, (x, _) in zip(nodes, placement.positions)]
                    + [group.x + group.width for group in placement.groups]) + 32
        height = max([y + node.height for node, (_, y) in zip(nodes, placement.positions)]
                     + [group.y + group.height for group in placement.groups]) + 32
        return max(680, width), height

    @staticmethod
    def _peak_space_tracks(intervals) -> int:
        """Reuse a track when projected route intervals do not overlap."""
        events = []
        for start, end in intervals:
            low, high = sorted((start, end))
            # Even a straight reference needs room around its horizontal run.
            events.extend(((low - 6, 1), (high + 6, -1)))
        active = peak = 0
        for _, delta in sorted(events):
            active += delta
            peak = max(peak, active)
        return peak

    def _space_demand_gap(self, minimum, maximum, intervals, spacing):
        tracks = self._peak_space_tracks(intervals)
        return min(maximum, minimum + 18 * max(0, tracks - 1) * spacing)

    def _space_connections(self, nodes, edges, positions):
        """Project the same row/header port spread used by the actual router."""
        relevant = [(a, b, row) for a, b, row in edges if a in positions and b in positions]
        outgoing = defaultdict(list)
        incoming = defaultdict(list)
        for index, (a, b, row) in enumerate(relevant):
            outgoing[a, row].append(index)
            incoming[b].append(index)
        starts = {}
        for (a, row), indices in outgoing.items():
            center = positions[a][1] + self._row_offset(nodes[a], row)
            step = 18 if row is not None else 12
            indices.sort(key=lambda index: (positions[relevant[index][1]][1], index))
            for order, index in enumerate(indices):
                starts[index] = (positions[a][0] + nodes[a].width + 18,
                                 center + step * (order - (len(indices) - 1) / 2))
        ends = {}
        for b, indices in incoming.items():
            indices.sort(key=lambda index: (starts[index][1], index))
            center = positions[b][1] + nodes[b].header_height / 2
            for order, index in enumerate(indices):
                ends[index] = (positions[b][0] - 18, center + 12 * (order - (len(indices) - 1) / 2))
        return [(a, b, starts[index], ends[index]) for index, (a, b, _) in enumerate(relevant)]

    def _align_space_cards(self, columns, nodes, edges, local, gaps, header):
        """Align references while preserving order and bounded local gaps."""
        membership = {i: j for j, column in enumerate(columns) for i in column}
        cross = [(a, b, row) for a, b, row in edges
                 if a in membership and b in membership and membership[a] != membership[b]]
        if not cross:
            return
        offsets = [0.0] * len(columns)
        limit = max(y + nodes[i].height for i, (_, y) in local.items())
        for _ in range(3):
            for j in range(1, len(columns)):
                deltas = []
                for a, b, row in cross:
                    delta = (local[a][1] + offsets[membership[a]] + self._row_offset(nodes[a], row)
                             - local[b][1] - offsets[membership[b]] - nodes[b].header_height / 2)
                    if membership[b] == j:
                        deltas.append(delta)
                    elif membership[a] == j:
                        deltas.append(-delta)
                if deltas:
                    offsets[j] = min(limit, max(-limit, offsets[j] + median(deltas)))
        minimum = min(offsets)
        for i, (x, y) in local.items():
            local[i] = x, y + offsets[membership[i]] - minimum
        # Whole-column alignment cannot accommodate references to different
        # rows in differently sized cards. Coordinate sweeps adjust each gap.
        for _ in range(3):
            for reverse in (True, False):
                preferences = defaultdict(list)
                for a, b, row in cross:
                    preferences[a].append(local[b][1] + nodes[b].header_height / 2
                                          - self._row_offset(nodes[a], row))
                    preferences[b].append(local[a][1] + self._row_offset(nodes[a], row)
                                          - nodes[b].header_height / 2)
                for c, column in enumerate(columns):
                    for r in (range(len(column) - 1, -1, -1) if reverse else range(len(column))):
                        i = column[r]
                        if not preferences[i]:
                            continue
                        lower, upper = float(header), math.inf
                        if r:
                            previous = column[r - 1]
                            bottom = local[previous][1] + nodes[previous].height
                            lower, upper = max(lower, bottom + gaps[c][r - 1]), bottom + 120
                        if r + 1 < len(column):
                            following = local[column[r + 1]][1] - nodes[i].height
                            lower = max(lower, following - 120)
                            upper = min(upper, following - gaps[c][r])
                        if lower <= upper:
                            local[i] = local[i][0], min(upper, max(lower, median(preferences[i])))

    def _pack_space(self, panels, nodes, edges, lane_count: int, align: bool,
                    spacing: float = 1.0) -> Placement:
        from .layout import FileGroupLayout, wrap_text

        framed = any(node.node.source_file for node in nodes)
        padding = 24 if framed else 0
        records = []
        for name, columns in panels:
            widths = [max(nodes[i].width for i in column) for column in columns]
            membership = {i: j for j, column in enumerate(columns) for i in column}
            internal = [(a, b, row) for a, b, row in edges if a in membership and b in membership]
            gaps = []
            for column in columns:
                column_gaps = []
                for r in range(len(column) - 1):
                    above, below = set(column[:r + 1]), set(column[r + 1:])
                    neighbors = {column[r], column[r + 1]}
                    intervals = [(nodes[a].width + 18, -18) for a, b, _ in internal
                                 if ((a in above and b in below) or (b in above and a in below))
                                 and (a in neighbors or b in neighbors)]
                    column_gaps.append(self._space_demand_gap(40, 120, intervals, spacing))
                gaps.append(column_gaps)
            width = sum(widths) + 72 * (len(columns) - 1) + 2 * padding
            title = name or "Types"
            lines = wrap_text(title, max(1, int((width - 190) / (15 * .67))))
            header = 44 + 19 * max(0, len(lines) - 1) if framed else 0
            local = {}
            for c, column in enumerate(columns):
                y = float(header)
                for r, i in enumerate(column):
                    local[i] = 0.0, y
                    y += nodes[i].height + (gaps[c][r] if r < len(gaps[c]) else 0)
            if align and len(columns) > 1:
                self._align_space_cards(columns, nodes, internal, local, gaps, header)
            gutter_intervals = [[] for _ in range(len(columns) - 1)]
            for a, b, start, end in self._space_connections(nodes, internal, local):
                if membership[a] != membership[b]:
                    # A long reference needs a vertical turn in one gutter;
                    # counting it in every intermediate gutter wastes width.
                    boundary = membership[a] if membership[a] < membership[b] else membership[a] - 1
                    gutter_intervals[boundary].append((start[1], end[1]))
                elif gutter_intervals:
                    # References within a column still need a side channel.
                    # Self references turn above or below their own card.
                    boundary = min(membership[a], len(gutter_intervals) - 1)
                    low, high = sorted((start[1], end[1]))
                    if a == b:
                        top = local[a][1] - 18
                        bottom = local[a][1] + nodes[a].height + 18
                        if start[1] + end[1] - 2 * top <= 2 * bottom - start[1] - end[1]:
                            low = min(low, top)
                        else:
                            high = max(high, bottom)
                    gutter_intervals[boundary].append((low, high))
            gutters = [self._space_demand_gap(72, 360, intervals, spacing) for intervals in gutter_intervals]
            width = sum(widths) + sum(gutters) + 2 * padding
            lines = wrap_text(title, max(1, int((width - 190) / (15 * .67))))
            actual_header = 44 + 19 * max(0, len(lines) - 1) if framed else 0
            x = float(padding)
            for c, column in enumerate(columns):
                for i in column:
                    local[i] = x, local[i][1] + actual_header - header
                x += widths[c] + (gutters[c] if c < len(gutters) else 0)
            header = actual_header
            height = max(y + nodes[i].height for i, (_, y) in local.items()) + padding
            records.append((name, width, height, header, lines, local))
        lanes = [[] for _ in range(lane_count)]
        heights = [0.0] * lane_count
        for record in records:
            lane = min(range(lane_count), key=lambda j: (heights[j], j))
            lanes[lane].append(record)
            heights[lane] += record[2] + 64
        positions = [(0.0, 0.0)] * len(nodes)
        panel_records = []
        lane_members = []
        for lane_index, lane in enumerate(lanes):
            membership = {i: r for r, record in enumerate(lane) for i in record[5]}
            lane_members.append(set(membership))
            intervals = [[] for _ in range(len(lane) - 1)]
            for a, b, _ in edges:
                if a not in membership or b not in membership or membership[a] == membership[b]:
                    continue
                source = lane[membership[a]][5][a][0] + nodes[a].width + 18
                target = lane[membership[b]][5][b][0] - 18
                first, last = sorted((membership[a], membership[b]))
                for boundary in {first, last - 1}:
                    intervals[boundary].append((source, target))
            y = 132.0
            for r, (name, width, height, header, lines, local) in enumerate(lane):
                for i, (nx, ny) in local.items():
                    positions[i] = nx, y + ny
                panel_records.append((lane_index, name, y, width, height, header, lines, local))
                y += height + (self._space_demand_gap(64, 192, intervals[r], spacing)
                               if r < len(intervals) else 0)
        membership = {i: j for j, members in enumerate(lane_members) for i in members}
        intervals = [[] for _ in range(lane_count - 1)]
        for a, b, start, end in self._space_connections(nodes, edges, dict(enumerate(positions))):
            if membership[a] != membership[b]:
                boundary = membership[a] if membership[a] < membership[b] else membership[a] - 1
                intervals[boundary].append((start[1], end[1]))
        xs = []
        x = 64.0
        for j, lane in enumerate(lanes):
            xs.append(x)
            x += max(record[1] for record in lane) + (self._space_demand_gap(96, 320, intervals[j], spacing)
                                                    if j < len(intervals) else 0)
        for i, (nx, ny) in enumerate(positions):
            positions[i] = xs[membership[i]] + nx, ny
        groups = [FileGroupLayout(name, name, xs[lane], y, width, height,
                                  [nodes[i].node.id for i in local], lines, header)
                  for lane, name, y, width, height, header, lines, local in panel_records if name]
        return Placement(positions, groups, nodes)

    def _space_proxy(self, placement, nodes, edges) -> float:
        from .layout import _text_height

        connections = []
        length = 0.0
        backward = 0
        for a, b, row in edges:
            ax, ay = placement.positions[a]
            bx, by = placement.positions[b]
            start = ax + nodes[a].width, ay + self._row_offset(nodes[a], row)
            end = bx, by + nodes[b].header_height / 2
            connections.append((start, end))
            length += abs(start[0] - end[0]) + abs(start[1] - end[1])
            backward += start[0] >= end[0]

        def side(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        sample = connections[:128]
        crossings = sum(side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0
                        for i, (a, b) in enumerate(sample) for c, d in sample[i + 1:])
        width, height = self._canvas(placement, nodes)
        return (crossings / self._edge_count + (len(edges) + backward) / self._edge_count
                + length / (self._edge_count * self._diagonal)
                + self._space_cost(width, height) + _text_height(nodes) / self._natural_text)


LAYOUTS: dict[str, type[LayoutEngine]] = {
    "default": DefaultLayout,
    "optimized": OptimizedLayout,
    "optimized-with-space": OptimizedWithSpaceLayout,
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
