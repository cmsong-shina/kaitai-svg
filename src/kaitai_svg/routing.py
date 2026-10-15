"""Pluggable obstacle-avoiding orthogonal connector routers.

The routing anchors sit 18 units outside a source's right edge and a target's
left edge. Each returned path includes both anchors; layout adds the short
horizontal port stubs. Instances belong to a single diagram and reserve routes
as they are added. No external layout engine or random seed is required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
import heapq
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .layout import FileGroupLayout, NodeLayout

Point = tuple[float, float]
Path = list[Point]
Connection = tuple[Point, Point]
PORT_OFFSET = 18


class Router(ABC):
    """Interface for a router constructed once per diagram.

    ``anchors`` contains every start/end anchor in connection order. Implement
    ``route`` to reserve and return an orthogonal, obstacle-free anchor path.
    Override ``route_all`` to optimize connections together, keeping the output
    in input order. Node rectangles and file-header bands are hard obstacles;
    port stubs are attached by the layout after routing.
    """

    @abstractmethod
    def __init__(self, nodes: list[NodeLayout], anchors: list[Point],
                 groups: list[FileGroupLayout] | None = None):
        pass

    @abstractmethod
    def route(self, start: Point, end: Point) -> Path:
        """Find and reserve one connector between the given anchors."""
        raise NotImplementedError

    def route_all(self, connections: Sequence[Connection]) -> list[Path]:
        """Route a diagram in input order; output paths retain that order."""
        return [self.route(start, end) for start, end in connections]


class DefaultRouter(Router):
    """A* on a sparse orthogonal grid with hard rectangle clearance."""

    def __init__(self, nodes: list[NodeLayout], anchors: list[tuple[float, float]],
                 groups: list[FileGroupLayout] | None = None):
        xs: set[float] = {24}
        ys: set[float] = {114}
        self.obstacles = []
        for node in nodes:
            xs.update((node.x - 18, node.x + node.width + 18))
            ys.update((node.y - 18, node.y + node.height + 18))
            self.obstacles.append((node.x - 8, node.y - 8, node.x + node.width + 8, node.y + node.height + 8))
        for group in groups or []:
            # File names and definition counts are part of the diagram, too.
            # Protect their complete header band, including wrapped titles,
            # while leaving the panel's side padding open for import routes.
            left, right = group.x + 16, group.x + group.width - 16
            top, bottom = group.y + 8, group.y + group.header_height - 8
            self.obstacles.append((left, top, right, bottom))
            xs.update((left - 10, right + 10))
            ys.update((top - 10, bottom + 10))
        for x, y in anchors:
            xs.add(x)
            ys.add(y)
        if nodes:
            # Stacked file panels need several independent outer lanes for
            # long imports and backward edges, just as horizontal gutters do.
            leftmost = min(n.x for n in nodes)
            rightmost = max(n.x + n.width for n in nodes)
            xs.update(leftmost - offset for offset in (18, 40, 62, 84) if leftmost - offset >= 12)
            xs.update(rightmost + offset for offset in (18, 40, 62))
            ys.add(max(n.y + n.height for n in nodes) + 54)
        # Give every gutter evenly spaced tracks, including wider gutters for
        # high fan-out. The outer tracks also serve cycles and self references.
        column_right: dict[float, float] = {}
        for node in nodes:
            column_right[node.x] = max(column_right.get(node.x, 0), node.x + node.width)
        columns = sorted(column_right)
        for left, right in zip(columns, columns[1:]):
            inner_left, inner_right = column_right[left] + 18, right - 18
            if inner_left >= inner_right:
                continue
            count = max(1, int((inner_right - inner_left) // 18))
            xs.update(inner_left + (inner_right - inner_left) * index / count for index in range(count + 1))
        self.xs, self.ys = sorted(xs), sorted(ys)
        self.x_index = {value: index for index, value in enumerate(self.xs)}
        self.y_index = {value: index for index, value in enumerate(self.ys)}
        self.clear: dict[tuple[tuple[int, int], tuple[int, int]], bool] = {}
        self.usage: dict[tuple[tuple[int, int], tuple[int, int]], int] = defaultdict(int)
        self.vertex_usage: dict[tuple[int, int], set[int]] = defaultdict(set)

    def _key(self, a: tuple[int, int], b: tuple[int, int]):
        return (a, b) if a < b else (b, a)

    def _allowed(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        key = self._key(a, b)
        if key in self.clear:
            return self.clear[key]
        ax, ay, bx, by = self.xs[a[0]], self.ys[a[1]], self.xs[b[0]], self.ys[b[1]]
        allowed = True
        for left, top, right, bottom in self.obstacles:
            if ax == bx:
                if left < ax < right and max(min(ay, by), top) < min(max(ay, by), bottom):
                    allowed = False
                    break
            elif top < ay < bottom and max(min(ax, bx), left) < min(max(ax, bx), right):
                allowed = False
                break
        self.clear[key] = allowed
        return allowed

    def route(self, start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
        initial = (self.x_index[start[0]], self.y_index[start[1]], 0)
        target = (self.x_index[end[0]], self.y_index[end[1]])
        distance = {initial: 0.0}
        previous: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        queue = [(abs(start[0] - end[0]) + abs(start[1] - end[1]), 0.0, initial)]
        final = None
        while queue:
            _, cost, state = heapq.heappop(queue)
            if cost != distance.get(state):
                continue
            x, y, axis = state
            if (x, y) == target:
                final = state
                break
            for nx, ny, next_axis in ((x + 1, y, 1), (x - 1, y, 1), (x, y + 1, 2), (x, y - 1, 2)):
                if not (0 <= nx < len(self.xs) and 0 <= ny < len(self.ys)):
                    continue
                a, b = (x, y), (nx, ny)
                if not self._allowed(a, b):
                    continue
                key = self._key(a, b)
                length = abs(self.xs[x] - self.xs[nx]) + abs(self.ys[y] - self.ys[ny])
                used = self.usage[key]
                candidate = cost + length + (20 if axis and axis != next_axis else 0)
                # Shared lines hide which endpoints belong together. Prefer a
                # detour or an ordinary crossing over any collinear overlap.
                candidate += used * (600 + length * 12)
                if self.vertex_usage[b] - {next_axis}:
                    candidate += 30
                new_state = (nx, ny, next_axis)
                if candidate < distance.get(new_state, math.inf):
                    distance[new_state] = candidate
                    previous[new_state] = state
                    heuristic = abs(self.xs[nx] - end[0]) + abs(self.ys[ny] - end[1])
                    heapq.heappush(queue, (candidate + heuristic, candidate, new_state))
        if final is None:
            raise ValueError("Could not route a connector around the diagram nodes")
        states = [final]
        while states[-1] != initial:
            states.append(previous[states[-1]])
        states.reverse()
        for a, b in zip(states, states[1:]):
            self.usage[self._key(a[:2], b[:2])] += 1
            self.vertex_usage[b[:2]].add(b[2])
        return [(self.xs[x], self.ys[y]) for x, y, _ in states]


class OrthogonalRouter(DefaultRouter):
    """Congestion-aware A* with bounded, deterministic rip-up and rerouting.

    Rectangles and file captions remain hard obstacles. Length, bends (including
    the horizontal port stubs), crossings/touches and shared segments contribute
    to the search cost. Complete paths reserve both ends of every grid segment,
    so a crossing cannot disappear merely by approaching it from another side.
    Short connectors are routed first; two refinement passes keep only changes
    that improve the whole diagram, prioritizing separation over other costs.

    This is a heuristic for fixed node positions, not a guarantee of a globally
    minimum-crossing drawing. Work is bounded to at most three searches per edge.
    """

    bend_penalty = 35
    crossing_penalty = 160
    refinement_passes = 2
    parallel_clearance = 0.0

    def __init__(self, nodes: list[NodeLayout], anchors: list[Point],
                 groups: list[FileGroupLayout] | None = None):
        super().__init__(nodes, anchors, groups)
        xs, ys = set(self.xs), set(self.ys)
        # Include port positions so their complete stubs can reserve grid edges.
        xs.update(x - PORT_OFFSET for x, _ in anchors[::2])
        xs.update(x + PORT_OFFSET for x, _ in anchors[1::2])
        if nodes:
            left = min(node.x for node in nodes)
            right = max(node.x + node.width for node in nodes)
            top = min(node.y for node in nodes)
            bottom = max(node.y + node.height for node in nodes)
            for offset in (18, 30, 42, 54, 66, 78):
                if left - offset >= 12:
                    xs.add(left - offset)
                xs.add(right + offset)
                if top - offset >= 114:
                    ys.add(top - offset)
                ys.add(bottom + offset)
            # Additional horizontal tracks in gaps between cards complement the
            # vertical gutter tracks of DefaultRouter without a dense pixel grid.
            columns: dict[float, list[NodeLayout]] = defaultdict(list)
            for node in nodes:
                columns[node.x].append(node)
            for column in columns.values():
                ordered = sorted(column, key=lambda node: node.y)
                for upper, lower in zip(ordered, ordered[1:]):
                    low, high = upper.y + upper.height + 18, lower.y - 18
                    if high > low:
                        count = min(8, max(2, math.ceil((high - low) / 12)))
                        ys.update(low + (high - low) * i / count for i in range(1, count))
        self.xs, self.ys = sorted(xs), sorted(ys)
        self.x_index = {value: index for index, value in enumerate(self.xs)}
        self.y_index = {value: index for index, value in enumerate(self.ys)}
        self.clear.clear()  # Grid indices have changed.
        self._segment_use: dict[tuple[tuple[int, int], tuple[int, int]], int] = defaultdict(int)
        self._vertex_use: dict[tuple[int, int], int] = defaultdict(int)
        self._base_xs, self._base_ys = self.xs[:], self.ys[:]
        self._anchors = tuple(anchors)
        self._near_segment_use: dict[tuple[tuple[int, int], tuple[int, int]], float] = {}
        self._near_x: list[list[tuple[int, float]]] = []
        self._near_y: list[list[tuple[int, float]]] = []

    def set_parallel_clearance(self, clearance: float) -> None:
        """Reserve visual space around parallel routes, including fixed stubs.

        Configure before routing. Zero retains the original grid and objective;
        a positive distance adds escape tracks and penalizes close parallel
        segments without making otherwise valid connections impossible.
        """
        try:
            valid = (not isinstance(clearance, bool) and isinstance(clearance, (int, float))
                     and math.isfinite(clearance) and clearance >= 0)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("parallel_clearance must be a finite nonnegative number")
        if self._segment_use:
            raise ValueError("parallel clearance must be configured before routing")
        self.parallel_clearance = float(clearance)
        xs, ys = set(self._base_xs), set(self._base_ys)
        if clearance:
            # Add obstacle boundaries and one clearance offset around anchors.
            # More concentric obstacle tracks multiply the sparse grid without
            # helping the narrow gaps that need these extra escape routes.
            for left, top, right, bottom in self.obstacles:
                xs.update(x for x in (left, right) if x >= 12)
                ys.update(y for y in (top, bottom) if y >= 114)
            for x, y in self._anchors:
                xs.update(value for value in (x - clearance, x + clearance) if value >= 12)
                ys.update(value for value in (y - clearance, y + clearance) if value >= 114)
        self.xs, self.ys = sorted(xs), sorted(ys)
        self.x_index = {value: index for index, value in enumerate(self.xs)}
        self.y_index = {value: index for index, value in enumerate(self.ys)}
        self.clear.clear()
        self._near_segment_use.clear()

        def neighbours(values):
            return [[(other, 1 - abs(values[other] - value) / clearance)
                     for other in range(bisect_right(values, value - clearance),
                                        bisect_left(values, value + clearance))
                     if other != index]
                    for index, value in enumerate(values)] if clearance else []

        self._near_x, self._near_y = neighbours(self.xs), neighbours(self.ys)

    def _parallel_neighbours(self, a: tuple[int, int], b: tuple[int, int]):
        if a[1] == b[1]:
            for y, weight in self._near_y[a[1]]:
                yield self._key((a[0], y), (b[0], y)), weight
        else:
            for x, weight in self._near_x[a[0]]:
                yield self._key((x, a[1]), (x, b[1])), weight

    def _find(self, start: Point, end: Point) -> Path:
        # A horizontal incoming direction accounts for the source stub's bend.
        initial = (self.x_index[start[0]], self.y_index[start[1]], 1)
        target = (self.x_index[end[0]], self.y_index[end[1]])
        if initial[:2] == target:
            return [start]
        distance = {initial: 0.0}
        previous = {}
        queue = [(abs(start[0] - end[0]) + abs(start[1] - end[1]), 0.0, initial)]
        final = None
        while queue:
            _, cost, state = heapq.heappop(queue)
            if cost != distance.get(state):
                continue
            x, y, axis = state
            # Axis zero denotes a terminal state whose final stub bend has
            # already been charged, keeping A*'s early exit optimal for this cost.
            if axis == 0:
                final = state
                break
            for nx, ny, next_axis in ((x + 1, y, 1), (x - 1, y, 1),
                                      (x, y + 1, 2), (x, y - 1, 2)):
                if not (0 <= nx < len(self.xs) and 0 <= ny < len(self.ys)):
                    continue
                a, b = (x, y), (nx, ny)
                # Never reverse into either fixed port stub. Besides adding
                # ambiguity, such reversals would vanish during simplification.
                if state == initial and nx < x:
                    continue
                if b == target and nx < x:
                    continue
                if not self._allowed(a, b):
                    continue
                length = abs(self.xs[x] - self.xs[nx]) + abs(self.ys[y] - self.ys[ny])
                candidate = cost + length + (self.bend_penalty if axis != next_axis else 0)
                candidate += self._segment_use.get(self._key(a, b), 0) * (600 + 12 * length)
                if self.parallel_clearance:
                    candidate += self._near_segment_use.get(self._key(a, b), 0) * (600 + 12 * length)
                # Count a previously routed path once per occupied vertex,
                # including bends and stubs, independent of traversal direction.
                candidate += self.crossing_penalty * self._vertex_use.get(a, 0)
                arriving = b == target
                if arriving:
                    candidate += self.bend_penalty if next_axis != 1 else 0
                    candidate += self.crossing_penalty * self._vertex_use.get(b, 0)
                new_state = (nx, ny, 0 if arriving else next_axis)
                if candidate < distance.get(new_state, math.inf):
                    distance[new_state] = candidate
                    previous[new_state] = state
                    heuristic = abs(self.xs[nx] - end[0]) + abs(self.ys[ny] - end[1])
                    heapq.heappush(queue, (candidate + heuristic, candidate, new_state))
        if final is None:
            raise ValueError("Could not route a connector around the diagram nodes")
        states = [final]
        while states[-1] != initial:
            states.append(previous[states[-1]])
        return [(self.xs[x], self.ys[y]) for x, y, _ in reversed(states)]

    def _vertices(self, path: Path) -> list[tuple[int, int]]:
        """Expand the fixed port stubs on the same grid as the anchor path."""
        vertices = [(self.x_index[x], self.y_index[y]) for x, y in path]
        sx, sy = vertices[0]
        ex, ey = vertices[-1]
        source = self.x_index[path[0][0] - PORT_OFFSET]
        target = self.x_index[path[-1][0] + PORT_OFFSET]
        return ([(x, sy) for x in range(source, sx)] + vertices
                + [(x, ey) for x in range(ex + 1, target + 1)])

    def _reserve(self, path: Path, delta: int) -> None:
        vertices = self._vertices(path)
        for a, b in zip(vertices, vertices[1:]):
            key = self._key(a, b)
            self._segment_use[key] += delta
            if not self._segment_use[key]:
                del self._segment_use[key]
            if self.parallel_clearance:
                for neighbour, weight in self._parallel_neighbours(a, b):
                    used = self._near_segment_use.get(neighbour, 0.0) + delta * weight
                    if used <= 1e-10:
                        self._near_segment_use.pop(neighbour, None)
                    else:
                        self._near_segment_use[neighbour] = used
        for vertex in set(vertices):
            self._vertex_use[vertex] += delta
            if not self._vertex_use[vertex]:
                del self._vertex_use[vertex]

    def _shape_cost(self, path: Path) -> float:
        vertices = self._vertices(path)
        axes = [1 if a[1] == b[1] else 2 for a, b in zip(vertices, vertices[1:])]
        bends = sum(a != b for a, b in zip(axes, axes[1:]))
        length = math.fsum(abs(self.xs[a[0]] - self.xs[b[0]]) + abs(self.ys[a[1]] - self.ys[b[1]])
                           for a, b in zip(vertices, vertices[1:]))
        return length + self.bend_penalty * bends

    def _quality(self, paths: Sequence[Path]) -> tuple[float, ...]:
        shared = math.fsum(count * (count - 1) / 2
                           * (abs(self.xs[a[0]] - self.xs[b[0]]) + abs(self.ys[a[1]] - self.ys[b[1]]))
                           for (a, b), count in self._segment_use.items() if count > 1)
        contacts = sum(count * (count - 1) // 2 for count in self._vertex_use.values())
        aesthetic = self.crossing_penalty * contacts + math.fsum(self._shape_cost(path) for path in paths)
        # Quantize only comparisons, avoiding spurious improvements from the
        # summation order of fractional gutter coordinates after a rip-up.
        if self.parallel_clearance:
            # Each pair is present from both directions. The deficit is its
            # shared projection weighted by the missing fraction of clearance.
            near = math.fsum(count * self._near_segment_use.get((a, b), 0.0)
                             * (abs(self.xs[a[0]] - self.xs[b[0]])
                                + abs(self.ys[a[1]] - self.ys[b[1]])) / 2
                             for (a, b), count in self._segment_use.items())
            return round(shared, 8), round(near, 8), round(aesthetic, 8)
        return round(shared, 8), round(aesthetic, 8)

    def _congestion(self, path: Path) -> int:
        return sum(self._vertex_use[vertex] - 1 for vertex in set(self._vertices(path)))

    def route(self, start: Point, end: Point) -> Path:
        path = self._find(start, end)
        self._reserve(path, 1)
        return path

    def route_all(self, connections: Sequence[Connection]) -> list[Path]:
        if not connections:
            return []
        paths: list[Path] = [[] for _ in connections]
        order = sorted(range(len(connections)), key=lambda i: (
            abs(connections[i][0][0] - connections[i][1][0])
            + abs(connections[i][0][1] - connections[i][1][1]), i))
        for index in order:
            paths[index] = self.route(*connections[index])
        quality = self._quality(paths)
        for _ in range(self.refinement_passes):
            improved = False
            order = sorted(range(len(paths)), key=lambda i: (-self._congestion(paths[i]), i))
            for index in order:
                old = paths[index]
                self._reserve(old, -1)
                try:
                    candidate = self._find(*connections[index])
                except ValueError:
                    # The previous valid path remains available even if a
                    # specialized search cannot find a replacement.
                    self._reserve(old, 1)
                    continue
                self._reserve(candidate, 1)
                paths[index] = candidate
                candidate_quality = self._quality(paths)
                if candidate_quality < quality:
                    quality = candidate_quality
                    improved = True
                else:
                    self._reserve(candidate, -1)
                    self._reserve(old, 1)
                    paths[index] = old
            if not improved:
                break
        return paths


ROUTERS: dict[str, type[Router]] = {
    "default": DefaultRouter,
    "orthogonal": OrthogonalRouter,
}


def get_router(router: str | type[Router]) -> type[Router]:
    """Resolve a built-in name or an application-provided Router subclass."""
    if isinstance(router, str):
        try:
            return ROUTERS[router]
        except KeyError:
            raise ValueError(f"Unknown router {router!r}; choose from: {', '.join(ROUTERS)}") from None
    if isinstance(router, type) and issubclass(router, Router) and not router.__abstractmethods__:
        return router
    raise ValueError("router must be a built-in name or a concrete Router subclass")
