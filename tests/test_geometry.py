"""Exact geometric objectives used to choose among fully routed placements."""

import unittest

from kaitai_svg.geometry import PathMetrics, measure_paths, path_segments


class PathMetricsTests(unittest.TestCase):
    def test_empty_and_zero_length_paths_do_not_add_cost(self):
        for paths in ([], [[]], [[(1, 2)]], [[(1, 2), (1, 2), (1, 2)]]):
            with self.subTest(paths=paths):
                self.assertEqual(measure_paths(paths), PathMetrics(0, 0, 0, 0))

    def test_grid_subdivisions_and_duplicate_points_preserve_a_proper_crossing(self):
        horizontal = [(0, 0), (2, 0), (5, 0), (5, 0), (10, 0)]
        vertical = [(5, -5), (5, -1), (5, 0), (5, 2), (5, 5)]
        self.assertEqual(measure_paths([horizontal, vertical]), PathMetrics(0, 1, 0, 20))
        self.assertEqual(path_segments(horizontal), [((0, 0), (10, 0))])

    def test_actual_bends_and_reversals_keep_their_distance(self):
        elbow = [(0, 0), (2, 0), (10, 0), (10, 3), (10, 8)]
        self.assertEqual(measure_paths([elbow]), PathMetrics(0, 0, 1, 18))
        reversal = [(0, 0), (10, 0), (5, 0)]
        self.assertEqual(path_segments(reversal), [((0, 0), (10, 0)), ((10, 0), (5, 0))])
        self.assertEqual(measure_paths([reversal]), PathMetrics(0, 0, 1, 15))

    def test_endpoints_and_corners_are_not_proper_crossings(self):
        cases = [
            ([[(0, 0), (10, 0)], [(5, -5), (5, 0)]], PathMetrics(0, 0, 0, 15)),
            ([[(0, 0), (5, 0), (5, 5)], [(5, -5), (5, 0)]], PathMetrics(0, 0, 1, 15)),
            ([[(0, 0), (10, 0)], [(5, -5), (5, 0), (8, 0)]], PathMetrics(3, 0, 1, 18)),
            ([[(0, 0), (10, 0)], [(10, 0), (20, 0)]], PathMetrics(0, 0, 0, 20)),
        ]
        for paths, expected in cases:
            with self.subTest(paths=paths):
                self.assertEqual(measure_paths(paths), expected)

    def test_shared_length_is_pairwise_and_independent_of_direction(self):
        cases = [
            ([[(0, 0), (10, 0)], [(7, 0), (5, 0), (3, 0)]], PathMetrics(4, 0, 0, 14)),
            ([[(2, -3), (2, 6)], [(2, 4), (2, -1)]], PathMetrics(5, 0, 0, 14)),
            ([[(0, 0), (10, 0)], [(0, 1), (10, 1)]], PathMetrics(0, 0, 0, 20)),
            ([[(0, 0), (10, 0)]] * 3, PathMetrics(30, 0, 0, 30)),
        ]
        for paths, expected in cases:
            with self.subTest(paths=paths):
                self.assertEqual(measure_paths(paths), expected)
                self.assertEqual(measure_paths(list(reversed(paths))), expected)

    def test_crossings_are_distinct_intersections_per_path_pair(self):
        horizontal_detour = [(0, 0), (10, 0), (0, 0)]
        vertical = [(5, -5), (5, 15)]
        self.assertEqual(measure_paths([horizontal_detour, vertical]), PathMetrics(0, 1, 1, 40))
        u_shape = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertEqual(measure_paths([u_shape, vertical]), PathMetrics(0, 2, 2, 50))

    def test_quality_prioritizes_legibility_before_bends_and_distance(self):
        self.assertLess(PathMetrics(0, 8, 30, 500).quality, PathMetrics(1, 0, 0, 20).quality)
        self.assertLess(PathMetrics(0, 0, 30, 500).quality, PathMetrics(0, 1, 0, 20).quality)
        self.assertLess(PathMetrics(0, 0, 1, 500).quality, PathMetrics(0, 0, 2, 20).quality)
        self.assertLess(PathMetrics(0, 0, 1, 20).quality, PathMetrics(0, 0, 1, 500).quality)


if __name__ == "__main__":
    unittest.main()
