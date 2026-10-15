"""Adaptive spacing, reflow and bounded-width layout behavior."""

from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from kaitai_svg.geometry import PathMetrics
from kaitai_svg.layout import _measure, _reserve_port_space, layout_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.placement import (
    LAYOUTS, LayoutEngine, OptimizedWithSpaceLayout, Placement,
    VerticalOptimizedLayout,
)
from tests import test_optimized_layout as geometry_checks


def cards(count=4, *, long=False, grouped=False):
    return [Node(str(i), f"type_{i}", source_file=f"file_{i // 2}.ksy" if grouped else "",
                 rows=[Row("payload", "payload_identifier_" * (5 if long else 1),
                           "long_type_expression_" * (6 if long else 1))])
            for i in range(count)]


class SpaceLayoutTests(unittest.TestCase):
    assert_clear_geometry = geometry_checks.OptimizedLayoutTests.assert_clear_geometry

    def test_registration_and_legacy_score_hook(self):
        self.assertIs(LAYOUTS["optimized-with-space"], OptimizedWithSpaceLayout)
        self.assertTrue(OptimizedWithSpaceLayout.supports_width_limit)
        self.assertTrue(VerticalOptimizedLayout.supports_width_limit)
        self.assertFalse(LayoutEngine.supports_width_limit)
        engine = VerticalOptimizedLayout()
        metrics = PathMetrics(0, 2, 4, 100)
        self.assertEqual(engine.score(metrics, 700, 400),
                         engine.score_placement(metrics, 700, 400, Placement([], [])))

    def test_long_cells_receive_more_width_without_changing_semantics(self):
        short = layout_graph(Graph("Short", cards(), []), layout="optimized-with-space")
        graph = Graph("Long", cards(long=True), [])
        original = deepcopy(graph)
        drawing = layout_graph(graph, layout="optimized-with-space")
        self.assertEqual(graph, original)
        self.assertGreater(drawing.nodes[0].width, short.nodes[0].width)
        self.assertGreater(sum(column.width for column in drawing.nodes[0].columns
                               if column.key in {"type", "id"}),
                           sum(column.width for column in short.nodes[0].columns
                               if column.key in {"type", "id"}))
        for source, box in zip(graph.nodes, drawing.nodes):
            self.assertIs(box.node, source)
            self.assertEqual([row.row for row in box.rows], source.rows)
            self.assertAlmostEqual(sum(column.width for column in box.columns), box.width)
            self.assertIs(box.sections[0].rows[0], box.rows[0])
        self.assert_clear_geometry(drawing)

    def test_grouped_cycles_imports_and_self_edges_remain_clear_and_deterministic(self):
        nodes = cards(6, long=True, grouped=True)
        for node in nodes:
            node.source_file = "nested_directory/" * 6 + node.source_file
        graph = Graph("Groups", nodes, [Edge(str(a), str(b), "payload") for a, b in
                      [(0, 1), (1, 0), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (2, 2)]],
                      root_source_file=nodes[0].source_file)
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                drawing = layout_graph(graph, layout="optimized-with-space", router=router)
                self.assertEqual(drawing, layout_graph(graph, layout="optimized-with-space", router=router))
                self.assertEqual([edge.edge for edge in drawing.edges], graph.edges)
                self.assertEqual(len(drawing.groups), 3)
                self.assert_clear_geometry(drawing)

    def test_mixed_card_widths_reduce_unused_column_space(self):
        nodes = [Node(str(i), str(i), source_file="mixed.ksy", kind=kind,
                      rows=[Row(str(j), f"label_{j}", "0" if kind == "enum" else "u1",
                                kind="enum" if kind == "enum" else "field") for j in range(3)])
                 for i, kind in enumerate(("type", "type", "enum", "enum"))]
        graph = Graph("Different widths", nodes, [])
        baseline = layout_graph(graph, layout="optimized")
        drawing = layout_graph(graph, layout="optimized-with-space")

        def unused_column_space(candidate):
            widths = {}
            for box in candidate.nodes:
                widths[box.x] = max(widths.get(box.x, 0), box.width)
            return sum((widths[box.x] - box.width) * box.height for box in candidate.nodes)

        self.assertGreater(unused_column_space(baseline), 0)
        self.assertEqual(unused_column_space(drawing), 0)
        self.assertGreater(len({box.width for box in drawing.nodes}), 1)
        self.assert_clear_geometry(drawing)

    def test_traffic_expands_card_column_panel_and_lane_gaps(self):
        nodes = [_measure(node) for node in cards(2)]
        engine = OptimizedWithSpaceLayout()
        engine._internal_edges = {"": [], "a.ksy": [], "b.ksy": []}
        sparse, busy = [], [(0, 1, "payload")] * 12
        for panels, lane_count, dimension in (
            ([("", [[0], [1]])], 1, "column"),
            ([("", [[0, 1]])], 1, "card"),
            ([("a.ksy", [[0]]), ("b.ksy", [[1]])], 1, "panel"),
            ([("a.ksy", [[0]]), ("b.ksy", [[1]])], 2, "lane"),
        ):
            with self.subTest(dimension=dimension):
                small = engine._pack_space(panels, nodes, sparse, lane_count, False)
                large = engine._pack_space(panels, nodes, busy, lane_count, False)
                axis = 0 if dimension in {"column", "lane"} else 1
                size = nodes[0].width if axis == 0 else nodes[0].height
                small_gap = small.positions[1][axis] - small.positions[0][axis] - size
                large_gap = large.positions[1][axis] - large.positions[0][axis] - size
                self.assertGreater(large_gap, small_gap)
                bounds = {"column": (72, 360), "card": (40, 120),
                          "panel": (64, 192), "lane": (96, 320)}[dimension]
                self.assertGreaterEqual(small_gap, bounds[0])
                self.assertLessEqual(large_gap, bounds[1])

    def test_profiles_are_diverse_and_search_and_routing_are_bounded(self):
        graph = Graph("Many cards", cards(12, grouped=True), [])
        measured = [_measure(node) for node in graph.nodes]
        _reserve_port_space(measured, graph)
        engine = OptimizedWithSpaceLayout()
        with patch.object(engine, "_space_proxy", wraps=engine._space_proxy) as proxy:
            placements = engine.placements(graph, measured)
        self.assertLessEqual(proxy.call_count, 64)
        self.assertLessEqual(len(placements), 6)
        self.assertGreaterEqual(len({candidate.nodes[0].width for candidate in placements}), 3)
        for candidate in placements:
            self.assertEqual(len(candidate.positions), len(graph.nodes))
            self.assertEqual([box.node for box in candidate.nodes], graph.nodes)
        engine.set_width_limit(680)
        bounded = engine.placements(graph, measured)
        self.assertTrue(any(all(box.width == 454 for box in candidate.nodes)
                            and len({position[0] for position in candidate.positions}) == 1
                            for candidate in bounded))

    def test_width_cap_forces_reflow_and_keeps_actual_routes_inside_canvas(self):
        for grouped in (False, True):
            nodes = cards(4, long=True, grouped=grouped)
            graph = Graph("Narrow", nodes, [Edge(str(a), str(b), "payload")
                          for a, b in [(0, 1), (1, 2), (2, 3), (3, 0), (0, 0)]])
            natural = [_measure(node) for node in nodes]
            for router in ("default", "orthogonal"):
                with self.subTest(grouped=grouped, router=router):
                    drawing = layout_graph(graph, layout="optimized-with-space", router=router,
                                           layout_width=680)
                    self.assertLessEqual(drawing.width, 680)
                    self.assertLess(drawing.nodes[0].width, natural[0].width)
                    self.assert_clear_geometry(drawing)

    def test_empty_and_invalid_widths(self):
        empty = Graph("Empty", [], [])
        self.assertEqual((layout_graph(empty, layout="optimized-with-space").width,
                          layout_graph(empty, layout="optimized-with-space").height), (720, 260))
        self.assertEqual(layout_graph(empty, layout="optimized-with-space", layout_width=680).width, 680)
        for graph in (empty, Graph("Card", cards(1), [])):
            for width in (0, -1, 679, math.nan, math.inf, -math.inf, True, "900", 10 ** 400):
                with self.subTest(width=width, empty=not graph.nodes):
                    with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                        layout_graph(graph, layout="optimized-with-space", layout_width=width)


if __name__ == "__main__":
    unittest.main()
