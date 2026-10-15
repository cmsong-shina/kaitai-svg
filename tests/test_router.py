"""Public router contract and independent geometric quality regressions."""

from __future__ import annotations

from itertools import combinations
import math
from types import SimpleNamespace
import unittest

from kaitai_svg.layout import layout_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.routing import DefaultRouter, OrthogonalRouter, Router


def complete_path(connection, points):
    """Include the horizontal connections from actual node ports to anchors."""
    start, end = connection
    return [(start[0] - 18, start[1]), *points, (end[0] + 18, end[1])]


def segments(points):
    simplified = []
    for point in points:
        if simplified and simplified[-1] == point:
            continue
        if len(simplified) > 1:
            a, b = simplified[-2:]
            dx, dy = b[0] - a[0], b[1] - a[1]
            nx, ny = point[0] - b[0], point[1] - b[1]
            if dx * ny == dy * nx and dx * nx + dy * ny > 0:
                simplified.pop()
        simplified.append(point)
    return list(zip(simplified, simplified[1:]))


def bend_count(points):
    axes = [a[0] == b[0] for a, b in segments(points)]
    return sum(first != second for first, second in zip(axes, axes[1:]))


def crossing_count(first, second):
    """Count distinct proper crossings, independent of grid subdivisions."""
    intersections = set()
    for a, b in segments(first):
        for c, d in segments(second):
            if a[1] == b[1] and c[0] == d[0]:
                horizontal, vertical = (a, b), (c, d)
            elif a[0] == b[0] and c[1] == d[1]:
                horizontal, vertical = (c, d), (a, b)
            else:
                continue
            (left, right), (top, bottom) = horizontal, vertical
            x, y = top[0], left[1]
            if min(left[0], right[0]) < x < max(left[0], right[0]) and min(top[1], bottom[1]) < y < max(top[1], bottom[1]):
                intersections.add((x, y))
    return len(intersections)


def overlap_length(first, second):
    overlap = 0
    for a, b in segments(first):
        for c, d in segments(second):
            if a[0] == b[0] == c[0] == d[0]:
                axis = 1
            elif a[1] == b[1] == c[1] == d[1]:
                axis = 0
            else:
                continue
            overlap += max(0, min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                           - max(min(a[axis], b[axis]), min(c[axis], d[axis])))
    return overlap


def enters_rectangle(a, b, rectangle):
    left, top = rectangle.x, rectangle.y
    right, bottom = left + rectangle.width, top + rectangle.height
    if a[0] == b[0]:
        return left < a[0] < right and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom)
    return top < a[1] < bottom and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right)


class RouterTests(unittest.TestCase):
    def assert_valid_path(self, connection, path):
        self.assertEqual(path[0], connection[0])
        self.assertEqual(path[-1], connection[1])
        for point in path:
            self.assertTrue(all(math.isfinite(value) for value in point))
        for a, b in segments(path):
            self.assertTrue(a[0] == b[0] or a[1] == b[1], (a, b))

    def test_public_interface_is_abstract_and_builtins_implement_it(self):
        with self.assertRaises(TypeError):
            Router([], [])
        for implementation in (DefaultRouter, OrthogonalRouter):
            self.assertTrue(issubclass(implementation, Router))
            self.assertEqual(implementation([], []).route_all([]), [])

    def test_single_obstacle_requires_only_four_bends_including_ports(self):
        obstacle = SimpleNamespace(x=160, y=150, width=80, height=100)
        connection = ((100, 200), (300, 200))
        route = OrthogonalRouter([obstacle], list(connection)).route_all([connection])[0]
        self.assert_valid_path(connection, route)
        full = complete_path(connection, route)
        self.assertEqual(bend_count(full), 4)
        self.assertFalse(any(enters_rectangle(a, b, obstacle) for a, b in segments(full)))

    def test_file_header_is_an_obstacle_even_without_nodes(self):
        group = SimpleNamespace(x=100, y=120, width=300, height=250, header_height=80)
        caption = SimpleNamespace(x=116, y=128, width=268, height=64)
        connection = ((82, 160), (418, 160))
        path = OrthogonalRouter([], list(connection), [group]).route_all([connection])[0]
        self.assert_valid_path(connection, path)
        self.assertFalse(any(enters_rectangle(a, b, caption) for a, b in segments(path)))

    def test_port_stub_crossing_is_removed_with_only_two_additional_bends(self):
        # The first connection's target stub, x=180..198 at y=200, is
        # invisible to the legacy router. The second connector crosses it.
        connections = [
            ((100, 200), (180, 200)),
            ((190, 180), (190, 220)),
            ((70, 170), (210, 230)),
        ]
        anchors = [point for connection in connections for point in connection]
        results = []
        for implementation in (DefaultRouter, OrthogonalRouter):
            routes = implementation([], anchors).route_all(connections)
            self.assertEqual(len(routes), len(connections))
            full_paths = []
            for connection, path in zip(connections, routes):
                self.assert_valid_path(connection, path)
                full_paths.append(complete_path(connection, path))
            results.append(full_paths)
            for first, second in combinations(full_paths, 2):
                self.assertEqual(overlap_length(first, second), 0)
        baseline, improved = results
        self.assertEqual(sum(crossing_count(a, b) for a, b in combinations(baseline, 2)), 1)
        self.assertEqual(sum(crossing_count(a, b) for a, b in combinations(improved, 2)), 0)
        self.assertLessEqual(sum(map(bend_count, improved)), sum(map(bend_count, baseline)) + 2)
        self.assertEqual(OrthogonalRouter([], anchors).route_all(connections),
                         OrthogonalRouter([], anchors).route_all(connections))

    def test_staggered_obstacles_prefer_four_bends_over_a_six_bend_zigzag(self):
        obstacles = [
            SimpleNamespace(x=120, y=190, width=40, height=70),
            SimpleNamespace(x=210, y=180, width=40, height=60),
            SimpleNamespace(x=300, y=140, width=40, height=60),
        ]
        connection = ((80, 200), (440, 200))
        paths = []
        for implementation in (DefaultRouter, OrthogonalRouter):
            path = implementation(obstacles, list(connection)).route_all([connection])[0]
            self.assert_valid_path(connection, path)
            full = complete_path(connection, path)
            paths.append(full)
            for a, b in segments(full):
                for obstacle in obstacles:
                    self.assertFalse(enters_rectangle(a, b, obstacle))
        self.assertEqual(bend_count(paths[0]), 6)
        self.assertEqual(bend_count(paths[1]), 4)

    def test_high_fanout_reduces_crossings_and_bends_without_merging_lines(self):
        graph = Graph("Many alternatives", [
            Node("source", "source", rows=[Row("choice", "choice", "switch")]),
            Node("target", "target"),
        ], [Edge("source", "target", "choice", label=f"case {index}") for index in range(12)])
        baseline = layout_graph(graph, router="default")
        improved = layout_graph(graph, router="orthogonal")
        for layout in (baseline, improved):
            self.assertEqual(len(layout.edges), 12)
            self.assertEqual(len({route.points[0] for route in layout.edges}), 12)
            self.assertEqual(len({route.points[-1] for route in layout.edges}), 12)
            for first, second in combinations(layout.edges, 2):
                self.assertEqual(overlap_length(first.points, second.points), 0)
        crossings = [sum(crossing_count(a.points, b.points) for a, b in combinations(layout.edges, 2))
                     for layout in (baseline, improved)]
        bends = [sum(bend_count(route.points) for route in layout.edges) for layout in (baseline, improved)]
        self.assertLess(crossings[1], crossings[0])
        self.assertLessEqual(bends[1], bends[0])

    def test_application_router_subclass_is_used_by_layout(self):
        instances = []

        class RecordingRouter(Router):
            def __init__(self, nodes, anchors, groups=None):
                self.delegate = DefaultRouter(nodes, anchors, groups)
                self.connections = []
                instances.append(self)

            def route(self, start, end):
                self.connections.append((start, end))
                return self.delegate.route(start, end)

        graph = Graph("Custom router", [Node("source", "source"), Node("target", "target")],
                      [Edge("source", "target")])
        layout = layout_graph(graph, router=RecordingRouter)
        self.assertEqual(layout, layout_graph(graph, router="default"))
        self.assertEqual(len(instances), 1)
        self.assertEqual(len(instances[0].connections), 1)
        start, end = instances[0].connections[0]
        self.assertEqual(start, (layout.edges[0].points[0][0] + 18, layout.edges[0].points[0][1]))
        self.assertEqual(end, (layout.edges[0].points[-1][0] - 18, layout.edges[0].points[-1][1]))

    def test_grouped_routes_avoid_wrapped_headers_nodes_and_canvas_bounds(self):
        nodes = [Node(str(index), f"type_{index}",
                      source_file="wrapped_source_directory/" * 18 + f"file_{index // 3}.ksy",
                      rows=[Row("field", "field", "u1")]) for index in range(9)]
        pairs = [(index, (index + 1) % 9) for index in range(9)] + [(6, 1), (4, 4), (0, 7)]
        graph = Graph("Grouped routing", nodes, [Edge(str(a), str(b), "field") for a, b in pairs])
        layout = layout_graph(graph, router="orthogonal")
        self.assertEqual(layout, layout_graph(graph, router="orthogonal"))
        self.assertEqual([route.edge for route in layout.edges], graph.edges)
        self.assertEqual(len(layout.groups), 3)
        captions = []
        for group in layout.groups:
            self.assertGreater(len(group.title_lines), 1)
            captions.append(SimpleNamespace(x=group.x + 16, y=group.y + 8,
                                            width=group.width - 32, height=group.header_height - 16))
        for route in layout.edges:
            for point in route.points:
                self.assertTrue(0 <= point[0] <= layout.width and 0 <= point[1] <= layout.height)
            for a, b in segments(route.points):
                self.assertTrue(a[0] == b[0] or a[1] == b[1])
                for obstacle in [*layout.nodes, *captions]:
                    self.assertFalse(enters_rectangle(a, b, obstacle), route.edge)

    def test_cycles_backward_links_and_fanout_preserve_ports_and_obstacles(self):
        nodes = [Node(str(index), f"type_{index}", rows=[Row("field", "field", "u1")]) for index in range(7)]
        pairs = [(0, 1), (0, 2), (0, 3), (1, 4), (2, 4), (3, 5), (4, 0), (5, 2), (2, 2), (5, 6)]
        graph = Graph("Routing constraints", nodes, [Edge(str(a), str(b), "field") for a, b in pairs])
        layout = layout_graph(graph, router="orthogonal")
        self.assertEqual(layout, layout_graph(graph, router="orthogonal"))
        self.assertEqual([route.edge for route in layout.edges], graph.edges)
        self.assertEqual(len({route.points[0] for route in layout.edges}), len(graph.edges))
        self.assertEqual(len({route.points[-1] for route in layout.edges}), len(graph.edges))
        by_id = {node.node.id: node for node in layout.nodes}
        for route in layout.edges:
            source, target = by_id[route.edge.source], by_id[route.edge.target]
            self.assertEqual(route.points[0][0], source.x + source.width)
            self.assertEqual(route.points[-1][0], target.x)
            for point in route.points:
                self.assertTrue(0 <= point[0] <= layout.width and 0 <= point[1] <= layout.height)
            for a, b in segments(route.points):
                self.assertTrue(a[0] == b[0] or a[1] == b[1])
                for obstacle in layout.nodes:
                    self.assertFalse(enters_rectangle(a, b, obstacle), (route.edge, obstacle.node.id))
        for first, second in combinations(layout.edges, 2):
            self.assertEqual(overlap_length(first.points, second.points), 0)


if __name__ == "__main__":
    unittest.main()
