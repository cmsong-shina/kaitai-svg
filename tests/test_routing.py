"""Regressions for connectors which previously merged into ambiguous lines."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
import unittest

from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Edge, Graph, Node, Row


def collinear_overlap(first, second) -> float:
    """Measure overlapping length between two complete orthogonal paths."""
    overlap = 0.0
    for a, b in zip(first.points, first.points[1:]):
        for c, d in zip(second.points, second.points[1:]):
            if a[0] == b[0] == c[0] == d[0]:
                axis = 1
            elif a[1] == b[1] == c[1] == d[1]:
                axis = 0
            else:
                continue
            overlap += max(
                0,
                min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                - max(min(a[axis], b[axis]), min(c[axis], d[axis])),
            )
    return overlap


class RoutingTests(unittest.TestCase):
    def assert_separate_paths(self, layout):
        for first, second in combinations(layout.edges, 2):
            self.assertEqual(
                collinear_overlap(first, second), 0,
                f"Connectors share a line: {first.edge} and {second.edge}",
            )

    def test_example_connectors_do_not_share_lines_or_attachment_points(self):
        graph = load_graph(Path(__file__).resolve().parent / "fixtures" / "packet.ksy")
        layout = layout_graph(graph)
        self.assertEqual(len(layout.edges), len(graph.edges))
        self.assert_separate_paths(layout)
        starts = [edge.points[0] for edge in layout.edges]
        ends = [edge.points[-1] for edge in layout.edges]
        self.assertEqual(len(set(starts)), len(starts))
        self.assertEqual(len(set(ends)), len(ends))

    def test_many_relationships_expand_their_field_and_header_for_distinct_ports(self):
        graph = Graph(
            "Many alternatives",
            [Node("source", "source", rows=[Row("choice", "choice", "switch")]), Node("target", "target")],
            [Edge("source", "target", "choice", label=f"case {index}") for index in range(12)],
        )
        layout = layout_graph(graph)
        source, target = layout.nodes
        row = source.rows[0]
        self.assert_separate_paths(layout)
        starts = sorted(edge.points[0][1] for edge in layout.edges)
        ends = sorted(edge.points[-1][1] for edge in layout.edges)
        for positions, top, bottom, spacing in (
            (starts, source.y + row.y, source.y + row.y + row.height, 18),
            (ends, target.y, target.y + target.header_height, 12),
        ):
            self.assertGreaterEqual(positions[0] - top, 12)
            self.assertGreaterEqual(bottom - positions[-1], 12)
            for previous, following in zip(positions, positions[1:]):
                self.assertGreaterEqual(following - previous, spacing)
        self.assertEqual(target.header_height, 156)
        self.assertEqual(layout, layout_graph(graph))


if __name__ == "__main__":
    unittest.main()
