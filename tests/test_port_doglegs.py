"""Complete-path cleanup shortens only safe, redundant terminal stairs."""

from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kaitai_svg.geometry import measure_paths, parallel_conflict_length
from kaitai_svg.layout import (
    EdgeLayout, FileGroupLayout, _measure, _route_edges, _shorten_port_doglegs,
)
from kaitai_svg.model import Edge, Graph, Node
from kaitai_svg.routing import OrthogonalRouter


def stair(port_y=228):
    return [(500, 500), (518, 500), (518, 400), (190, 400), (190, 230),
            (182, 230), (182, port_y), (200, port_y)]


def route(points, source="source", target="target"):
    return EdgeLayout(Edge(source, target), list(points))


class PortDoglegTests(unittest.TestCase):
    def test_target_stair_shortens_stub_and_preserves_real_ports(self):
        edge = route(stair())
        semantic_edge = edge.edge
        original = deepcopy(edge)
        nodes = [SimpleNamespace(x=200, y=200, width=100, height=150)]
        _shorten_port_doglegs([edge], nodes, [], 8)
        self.assertEqual(edge.points, [(500, 500), (518, 500), (518, 400), (190, 400), (190, 228), (200, 228)])
        self.assertEqual(edge.points[0], original.points[0])
        self.assertEqual(edge.points[-1], original.points[-1])
        self.assertIs(edge.edge, semantic_edge)
        self.assertEqual(measure_paths([edge.points]).bends, 4)
        self.assertLess(measure_paths([edge.points]).length, measure_paths([original.points]).length)

    def test_source_stair_is_shortened_symmetrically(self):
        points = [(800 - x, y) for x, y in reversed(stair())]
        edge = route(points)
        _shorten_port_doglegs([edge], [], [], 8)
        self.assertEqual(edge.points[0], points[0])
        self.assertEqual(edge.points[-1], points[-1])
        self.assertEqual(edge.points[:2], [(600, 228), (610, 228)])
        self.assertEqual(measure_paths([edge.points]).bends, 4)

    def test_new_vertical_extension_must_clear_expanded_card_and_file_header(self):
        obstacle = SimpleNamespace(x=194, y=214, width=1, height=1)
        header = FileGroupLayout("file.ksy", "file.ksy", 170, 190, 70, 100, [], ["file.ksy"], 44)
        for nodes, groups in (([obstacle], []), ([], [header])):
            with self.subTest(nodes=nodes, groups=groups):
                edge = route(stair(190))
                before = list(edge.points)
                _shorten_port_doglegs([edge], nodes, groups, 8)
                self.assertEqual(edge.points, before)

    def test_new_shared_near_parallel_or_proper_crossing_is_rejected(self):
        for other in ([(190, 228.25), (190, 229.75)],
                      [(197, 228.25), (197, 229.75)],
                      [(188, 210), (192, 210)]):
            with self.subTest(other=other):
                edge = route(stair(190 if other[0][1] == 210 else 228))
                before = list(edge.points)
                edges = [edge, route(other, "other_source", "other_target")]
                _shorten_port_doglegs(edges, [], [], 8)
                self.assertEqual(edge.points, before)

    def test_shortcut_must_not_cross_its_own_earlier_segments(self):
        points = [(500, 500), (518, 500), (518, 210), (186, 210), (186, 220),
                  (500, 220), (500, 400), (190, 400), (190, 230),
                  (182, 230), (182, 190), (200, 190)]
        edge = route(points)
        _shorten_port_doglegs([edge], [], [], 8)
        self.assertEqual(edge.points, points)

    def test_legitimate_turn_and_insufficient_port_straight_are_unchanged(self):
        for points in (
            [(500, 500), (518, 500), (518, 400), (170, 400), (170, 230),
             (182, 230), (182, 228), (200, 228)],
            [(500, 500), (518, 500), (518, 400), (194, 400), (194, 230),
             (182, 230), (182, 228), (200, 228)],
        ):
            with self.subTest(points=points):
                edge = route(points)
                _shorten_port_doglegs([edge], [], [], 8)
                self.assertEqual(edge.points, points)

    def test_safe_cleanup_preserves_existing_parallel_clearance_and_is_idempotent(self):
        edges = [route(stair()), route([(210, 235), (210, 390)], "other_source", "other_target")]
        before = measure_paths([edge.points for edge in edges])
        _shorten_port_doglegs(edges, [], [], 8)
        paths = [edge.points for edge in edges]
        self.assertEqual(measure_paths(paths).shared_length, before.shared_length)
        self.assertEqual(parallel_conflict_length(paths), 0)
        self.assertLess(measure_paths(paths).bends, before.bends)
        snapshot = deepcopy(paths)
        _shorten_port_doglegs(edges, [], [], 8)
        self.assertEqual([edge.points for edge in edges], snapshot)

    def test_router_cleanup_is_opt_in_after_routing(self):
        graph = Graph("Ports", [Node("source", "source"), Node("target", "target")],
                      [Edge("source", "target")])
        nodes = [_measure(node) for node in graph.nodes]
        nodes[0].x, nodes[0].y = 64, 132
        nodes[1].x, nodes[1].y = 800, 132
        outgoing, incoming = defaultdict(list), defaultdict(list)
        outgoing["source", None].append(0)
        incoming["target"].append(0)
        with patch("kaitai_svg.layout._shorten_port_doglegs") as cleanup:
            _route_edges(graph, nodes, [], OrthogonalRouter, outgoing, incoming)
            cleanup.assert_not_called()
            _route_edges(graph, nodes, [], OrthogonalRouter, outgoing, incoming, parallel_clearance=8)
            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
