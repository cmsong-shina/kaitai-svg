"""Grid-independent measures for complete orthogonal connector paths."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from collections.abc import Sequence

Point = tuple[float, float]


def path_segments(path: Sequence[Point]) -> list[tuple[Point, Point]]:
    """Remove redundant grid subdivisions, retaining true bends/reversals."""
    points: list[Point] = []
    for point in path:
        if points and points[-1] == point:
            continue
        while len(points) > 1:
            a, b = points[-2:]
            dx, dy = b[0] - a[0], b[1] - a[1]
            nx, ny = point[0] - b[0], point[1] - b[1]
            if dx * ny != dy * nx or dx * nx + dy * ny <= 0:
                break
            points.pop()
        points.append(point)
    return list(zip(points, points[1:]))


@dataclass(frozen=True)
class PathMetrics:
    shared_length: float
    crossings: int
    bends: int
    length: float

    @property
    def quality(self) -> tuple[float, int, int, float]:
        """Minimize hidden/shared routes, crossings, bends, then distance."""
        return round(self.shared_length, 8), self.crossings, self.bends, round(self.length, 8)


def measure_paths(paths: Sequence[Sequence[Point]]) -> PathMetrics:
    """Count proper crossings and pairwise shared length in SVG coordinates."""
    segments = [path_segments(path) for path in paths]
    bends = sum(max(0, len(path) - 1) for path in segments)
    length = math.fsum(abs(a[0] - b[0]) + abs(a[1] - b[1])
                       for path in segments for a, b in path)
    shared: list[float] = []
    crossings = 0
    for first, second in combinations(segments, 2):
        intersections = set()
        for a, b in first:
            for c, d in second:
                axis = (0 if a[1] == b[1] == c[1] == d[1] else
                        1 if a[0] == b[0] == c[0] == d[0] else None)
                if axis is not None:
                    shared.append(max(0, min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                                      - max(min(a[axis], b[axis]), min(c[axis], d[axis]))))
                    continue
                if a[1] == b[1] and c[0] == d[0]:
                    h1, h2, v1, v2 = a, b, c, d
                elif a[0] == b[0] and c[1] == d[1]:
                    h1, h2, v1, v2 = c, d, a, b
                else:
                    continue
                if (min(h1[0], h2[0]) < v1[0] < max(h1[0], h2[0])
                        and min(v1[1], v2[1]) < h1[1] < max(v1[1], v2[1])):
                    intersections.add((v1[0], h1[1]))
        crossings += len(intersections)
    return PathMetrics(math.fsum(shared), crossings, bends, length)


def parallel_conflict_length(paths: Sequence[Sequence[Point]], clearance: float = 8.0) -> float:
    """Measure parallel routes that are close enough to obscure each other.

    Overlapping projections count in proportion to their clearance deficit:
    exact shared lines count fully, while lines at least ``clearance`` apart
    contribute nothing. Only segments belonging to distinct paths are compared.
    Redundant grid subdivisions do not change the result.
    """
    try:
        valid = (not isinstance(clearance, bool) and isinstance(clearance, (int, float))
                 and math.isfinite(clearance) and clearance >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("clearance must be a finite nonnegative number")
    if clearance == 0:
        return 0.0
    conflicts = []
    for first, second in combinations([path_segments(path) for path in paths], 2):
        for a, b in first:
            for c, d in second:
                axis = (0 if a[1] == b[1] and c[1] == d[1] else
                        1 if a[0] == b[0] and c[0] == d[0] else None)
                if axis is None:
                    continue
                distance = round(abs(a[1 - axis] - c[1 - axis]), 8)
                if distance >= clearance:
                    continue
                overlap = round(min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                                - max(min(a[axis], b[axis]), min(c[axis], d[axis])), 8)
                if overlap > 0:
                    conflicts.append(overlap * (1 - distance / clearance))
    return round(math.fsum(conflicts), 8)
