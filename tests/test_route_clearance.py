"""Opt-in parallel clearance separates visible strokes and their port stubs."""

import unittest

from kaitai_svg.geometry import measure_paths, parallel_conflict_length
from kaitai_svg.routing import OrthogonalRouter


def complete_paths(connections, paths):
    return [[(start[0] - 18, start[1]), *path, (end[0] + 18, end[1])]
            for (start, end), path in zip(connections, paths)]


class RouteClearanceTests(unittest.TestCase):
    def route(self, connections, clearance=None):
        anchors = [point for connection in connections for point in connection]
        router = OrthogonalRouter([], anchors)
        if clearance is not None:
            router.set_parallel_clearance(clearance)
        paths = router.route_all(connections)
        for (start, end), path in zip(connections, paths):
            self.assertEqual(path[0], start)
            self.assertEqual(path[-1], end)
            self.assertTrue(all(a[0] == b[0] or a[1] == b[1]
                                for a, b in zip(path, path[1:])))
        return router, paths, complete_paths(connections, paths)

    def test_zero_clearance_preserves_default_grid_routes_and_objective(self):
        connections = [((100, 200), (400, 200)), ((200, 202), (500, 202))]
        ordinary, ordinary_paths, _ = self.route(connections)
        zero, zero_paths, _ = self.route(connections, 0)
        self.assertEqual(ordinary.parallel_clearance, 0)
        self.assertEqual((ordinary.xs, ordinary.ys), (zero.xs, zero.ys))
        self.assertEqual(ordinary_paths, zero_paths)
        self.assertEqual(ordinary._quality(ordinary_paths), zero._quality(zero_paths))
        self.assertEqual(len(zero._quality(zero_paths)), 2)

    def test_close_horizontal_and_vertical_routes_gain_eight_pixel_clearance(self):
        cases = (
            [((100, 200), (400, 200)), ((200, 202), (500, 202))],
            [((100, 180), (100, 480)), ((102, 280), (102, 580))],
            [((100, 200), (400, 200)), ((200, 202), (500, 202)),
             ((300, 204), (600, 204))],
        )
        for connections in cases:
            with self.subTest(connections=connections):
                _, _, baseline = self.route(connections)
                router, paths, full = self.route(connections, 8)
                self.assertGreater(parallel_conflict_length(baseline, 8), 0)
                self.assertEqual(measure_paths(full).shared_length, 0)
                self.assertAlmostEqual(parallel_conflict_length(full, 8), 0)
                self.assertAlmostEqual(router._quality(paths)[1],
                                       parallel_conflict_length(full, 8))
                self.assertEqual(paths, self.route(connections, 8)[1])

    def test_clearance_reservations_and_quality_include_fixed_port_stubs(self):
        connections = [((100, 200), (400, 200)), ((200, 202), (500, 202))]
        anchors = [point for connection in connections for point in connection]
        router = OrthogonalRouter([], anchors)
        router.set_parallel_clearance(8)
        paths = router.route_all(connections)
        # This middle section lies alongside the first connection's fixed
        # target stub, even though their anchor paths have no parallel overlap.
        end = router.x_index[418]
        row = router.y_index[202]
        self.assertGreater(router._near_segment_use.get(
            router._key((end - 1, row), (end, row)), 0), 0)
        for path in paths:
            router._reserve(path, -1)
        self.assertEqual(router._segment_use, {})
        self.assertEqual(router._vertex_use, {})
        self.assertEqual(router._near_segment_use, {})

    def test_unavoidable_close_stubs_do_not_make_connections_impossible(self):
        connections = [((100, 200), (400, 200)), ((100, 202), (400, 202))]
        router, paths, full = self.route(connections, 8)
        self.assertEqual(measure_paths(full).shared_length, 0)
        self.assertGreater(parallel_conflict_length(full, 8), 0)
        self.assertAlmostEqual(router._quality(paths)[1],
                               parallel_conflict_length(full, 8))


if __name__ == "__main__":
    unittest.main()
