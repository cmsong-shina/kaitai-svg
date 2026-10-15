"""Independent geometry and quality checks for interchangeable node placement."""

from copy import deepcopy
from itertools import combinations
import math
from types import SimpleNamespace
import unittest

from kaitai_svg.layout import layout_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.placement import DefaultLayout, LayoutEngine, OptimizedLayout, Placement
from kaitai_svg.routing import DefaultRouter, Router
from tests.test_router import crossing_count, enters_rectangle, segments


def overlaps(first, second):
    return (first.x < second.x + second.width and second.x < first.x + first.width
            and first.y < second.y + second.height and second.y < first.y + first.height)


def path_length(layout):
    return sum(abs(a[0] - b[0]) + abs(a[1] - b[1])
               for route in layout.edges for a, b in segments(route.points))


class OptimizedLayoutTests(unittest.TestCase):
    def assert_clear_geometry(self, drawing):
        for first, second in combinations(drawing.nodes, 2):
            self.assertFalse(overlaps(first, second), (first.node.id, second.node.id))
        for first, second in combinations(drawing.groups, 2):
            self.assertFalse(overlaps(first, second), (first.source_file, second.source_file))
        captions = [SimpleNamespace(x=group.x + 24, y=group.y + 10,
                                    width=group.width - 48, height=group.header_height - 18)
                    for group in drawing.groups]
        by_id = {box.node.id: box for box in drawing.nodes}
        for route in drawing.edges:
            source, target = by_id[route.edge.source], by_id[route.edge.target]
            self.assertEqual(route.points[0][0], source.x + source.width)
            self.assertEqual(route.points[-1][0], target.x)
            if route.edge.source_row is not None:
                row = next(row for row in source.rows if row.row.id == route.edge.source_row)
                self.assertLessEqual(source.y + row.y, route.points[0][1])
                self.assertLessEqual(route.points[0][1], source.y + row.y + row.height)
            else:
                self.assertLessEqual(source.y, route.points[0][1])
                self.assertLessEqual(route.points[0][1], source.y + source.header_height)
            self.assertLessEqual(target.y, route.points[-1][1])
            self.assertLessEqual(route.points[-1][1], target.y + target.header_height)
            for x, y in route.points:
                self.assertTrue(math.isfinite(x) and math.isfinite(y))
                self.assertTrue(0 <= x <= drawing.width and 0 <= y <= drawing.height)
            for a, b in segments(route.points):
                self.assertTrue(a[0] == b[0] or a[1] == b[1], (a, b))
                for box in [*drawing.nodes, *captions]:
                    self.assertFalse(enters_rectangle(a, b, box), (route.edge, box))

    def test_public_engines_and_legacy_default(self):
        with self.assertRaises(TypeError):
            LayoutEngine()
        for implementation in (DefaultLayout, OptimizedLayout):
            self.assertTrue(issubclass(implementation, LayoutEngine))
        graph = Graph("Default", [Node("root", "root"), Node("child", "child")],
                      [Edge("root", "child")])
        self.assertEqual(layout_graph(graph), layout_graph(graph, layout="default"))
        self.assertEqual(layout_graph(graph), layout_graph(graph, layout=DefaultLayout))
        for invalid in ("missing", LayoutEngine, object, object()):
            with self.subTest(layout=invalid), self.assertRaises(ValueError):
                layout_graph(graph, layout=invalid)

    def test_reorders_cards_to_remove_a_real_crossing_and_shorten_routes(self):
        # The height-only panel packer puts b above d, crossing a->d with c->b.
        # This checks complete routes rather than straight center-to-center links.
        graph = Graph("Crossed pairs", [
            Node(key, key, source_file="sample.ksy", rows=[Row("field", "field", "u1")])
            for key in "abcd"
        ], [Edge("a", "d", "field"), Edge("c", "b", "field")])
        baseline = layout_graph(graph, router="orthogonal", layout="default")
        improved = layout_graph(graph, router="orthogonal", layout="optimized")
        crossings = [sum(crossing_count(a.points, b.points) for a, b in combinations(drawing.edges, 2))
                     for drawing in (baseline, improved)]
        self.assertGreater(crossings[0], 0)
        self.assertEqual(crossings[1], 0)
        self.assertLess(path_length(improved), path_length(baseline))
        self.assertNotEqual([(node.x, node.y) for node in baseline.nodes],
                            [(node.x, node.y) for node in improved.nodes])
        self.assert_clear_geometry(improved)

    def test_group_membership_wrapped_headers_and_semantics_are_preserved(self):
        filenames = ["nested_source_directory/" * 12 + f"file_{index}.ksy" for index in range(3)]
        nodes = [Node(str(index), f"type_{index}", source_file=filenames[index // 2], rows=[
            Row("instance", "computed", "value", kind="instance"),
            Row("payload", "payload", "target", details="variable contents " * (index % 2)),
            Row("param", "length", "u4", kind="param"),
        ]) for index in range(6)]
        edges = [Edge(str(a), str(b), "payload") for a, b in
                 [(0, 3), (2, 1), (3, 4), (4, 5), (5, 0), (1, 1)]]
        graph = Graph("Grouped", nodes, edges)
        original = deepcopy(graph)
        drawing = layout_graph(graph, router="orthogonal", layout="optimized")
        self.assertEqual(graph, original)
        self.assertEqual(drawing, layout_graph(graph, router="orthogonal", layout="optimized"))
        self.assertEqual([box.node for box in drawing.nodes], graph.nodes)
        self.assertEqual([route.edge for route in drawing.edges], graph.edges)
        legacy = layout_graph(graph, layout="default")
        for measured, old in zip(drawing.nodes, legacy.nodes):
            self.assertIs(measured.node, old.node)
            self.assertEqual((measured.width, measured.height, measured.header_height),
                             (old.width, old.height, old.header_height))
            self.assertEqual(measured.rows, old.rows)
            self.assertEqual([row.row.id for row in measured.rows], ["param", "payload", "instance"])
        by_id = {box.node.id: box for box in drawing.nodes}
        self.assertEqual({group.source_file for group in drawing.groups}, set(filenames))
        for group in drawing.groups:
            self.assertGreater(len(group.title_lines), 1)
            self.assertEqual(set(group.node_ids), {node.id for node in nodes if node.source_file == group.source_file})
            for node_id in group.node_ids:
                box = by_id[node_id]
                self.assertGreaterEqual(box.x, group.x + 24)
                self.assertGreaterEqual(box.y, group.y + group.header_height)
                self.assertLessEqual(box.x + box.width, group.x + group.width - 24)
                self.assertLessEqual(box.y + box.height, group.y + group.height - 24)
        self.assert_clear_geometry(drawing)

    def test_ungrouped_cycles_self_edges_and_disconnected_nodes(self):
        nodes = [Node(key, key, rows=[Row("field", "field", "u1")]) for key in "abcde"]
        graph = Graph("General graph", nodes, [
            Edge("a", "b", "field"), Edge("b", "a", "field"),
            Edge("c", "a", "field"), Edge("c", "c", "field"),
            Edge("e", "b"),
        ])
        drawing = layout_graph(graph, router="orthogonal", layout="optimized")
        self.assertEqual(drawing.groups, [])
        self.assertEqual(drawing, layout_graph(graph, router="orthogonal", layout=OptimizedLayout))
        self.assertEqual([box.node for box in drawing.nodes], graph.nodes)
        self.assertEqual([route.edge for route in drawing.edges], graph.edges)
        self.assert_clear_geometry(drawing)

    def test_custom_placement_runs_before_routing_with_expanded_row_geometry(self):
        events = []
        positions = []
        measured_heights = []

        class ShiftedLayout(LayoutEngine):
            def placements(self, graph, nodes):
                events.append("layout")
                measured_heights.append(nodes[0].rows[0].height)
                original = DefaultLayout().placements(graph, nodes)[0]
                positions.extend((x + 120, y + 70) for x, y in original.positions)
                return [Placement(list(positions), original.groups)]

        class RecordingRouter(Router):
            def __init__(self, nodes, anchors, groups=None):
                events.append("router")
                self.delegate = DefaultRouter(nodes, anchors, groups)
                self.positions = [(node.x, node.y) for node in nodes]
                events.append(self.positions)

            def route(self, start, end):
                return self.delegate.route(start, end)

        graph = Graph("Custom", [Node("a", "a", rows=[Row("field", "field", "u1")]), Node("b", "b")],
                      [Edge("a", "b", "field") for _ in range(5)])
        drawing = layout_graph(graph, router=RecordingRouter, layout=ShiftedLayout)
        self.assertEqual(events, ["layout", "router", positions])
        self.assertEqual([(node.x, node.y) for node in drawing.nodes], positions)
        self.assertGreaterEqual(measured_heights[0], 24 + 18 * 4)
        self.assertGreaterEqual(drawing.nodes[0].rows[0].height, 24 + 18 * 4)
        self.assert_clear_geometry(drawing)

    def test_empty_edgeless_and_mixed_file_metadata(self):
        empty = layout_graph(Graph("Empty", [], []), layout="optimized")
        self.assertEqual((empty.nodes, empty.edges, empty.groups), ([], [], []))
        self.assertGreater(empty.width, 0)
        self.assertGreater(empty.height, 0)
        for nodes in ([Node("a", "a")],
                      [Node("a", "a", source_file="sample.ksy"), Node("b", "b")]):
            with self.subTest(nodes=nodes):
                drawing = layout_graph(Graph("No connectors", nodes, []), layout="optimized")
                self.assertEqual([box.node for box in drawing.nodes], nodes)
                self.assertEqual(drawing.edges, [])
                self.assert_clear_geometry(drawing)

    def test_selecting_an_earlier_candidate_restores_its_nodes_and_routes(self):
        class ExtraMarginLayout(LayoutEngine):
            def placements(self, graph, nodes):
                original = DefaultLayout().placements(graph, nodes)[0]
                shifted = Placement([(x + 400, y + 300) for x, y in original.positions], [])
                return [original, shifted]

        graph = Graph("Candidate selection", [Node("a", "a"), Node("b", "b")], [Edge("a", "b")])
        # Both candidates have equivalent routes. The first uses less canvas;
        # its geometry must survive evaluation of the second candidate.
        expected = layout_graph(graph, router="orthogonal", layout="default")
        drawing = layout_graph(graph, router="orthogonal", layout=ExtraMarginLayout)
        self.assertEqual(drawing, expected)
        self.assert_clear_geometry(drawing)


if __name__ == "__main__":
    unittest.main()
