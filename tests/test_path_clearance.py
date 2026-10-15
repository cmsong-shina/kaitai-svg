"""Physical route clearance is independent of sparse routing-grid detail."""

import math
import unittest

from kaitai_svg.geometry import parallel_conflict_length


class PathClearanceTests(unittest.TestCase):
    def test_long_parallel_routes_have_weighted_conflicts(self):
        paths = [[(0, 0), (100, 0)], [(20, 2), (80, 2)]]
        self.assertEqual(parallel_conflict_length(paths), 45)
        self.assertEqual(parallel_conflict_length(paths, 4), 30)
        self.assertEqual(parallel_conflict_length(paths, 0), 0)

    def test_vertical_reversed_paths_and_partial_overlap(self):
        paths = [[(10, 100), (10, 0)], [(12, 80), (12, 20)]]
        self.assertEqual(parallel_conflict_length(paths), 45)

    def test_clearance_boundary_and_disjoint_projections_have_no_conflict(self):
        for second in ([(0, 8), (100, 8)], [(100, 2), (200, 2)], [(120, 0), (200, 0)]):
            with self.subTest(second=second):
                self.assertEqual(parallel_conflict_length([[(0, 0), (100, 0)], second]), 0)
        self.assertEqual(parallel_conflict_length([[(0, 0), (100, 0)],
                                                 [(0, 8 - 1e-10), (100, 8 - 1e-10)]]), 0)

    def test_exact_sharing_counts_but_crossings_and_own_bends_do_not(self):
        self.assertEqual(parallel_conflict_length([[(0, 0), (100, 0)], [(20, 0), (80, 0)]]), 60)
        self.assertEqual(parallel_conflict_length([[(0, 0), (100, 0)], [(50, -10), (50, 10)]]), 0)
        self.assertEqual(parallel_conflict_length([[(0, 0), (100, 0), (100, 2), (0, 2)]]), 0)

    def test_grid_subdivisions_and_path_order_do_not_change_measure(self):
        simple = [[(0, 0), (100, 0)], [(20, 2), (80, 2)]]
        subdivided = [[(0, 0), (10, 0), (50, 0), (50, 0), (100, 0)],
                      [(20, 2), (25, 2), (60, 2), (80, 2)]]
        self.assertEqual(parallel_conflict_length(simple), parallel_conflict_length(subdivided))
        self.assertEqual(parallel_conflict_length(simple), parallel_conflict_length(simple[::-1]))

    def test_distinct_path_pairs_add_and_empty_paths_are_ignored(self):
        paths = [[(0, 0), (100, 0)], [(0, 2), (100, 2)], [(0, 4), (100, 4)]]
        self.assertEqual(parallel_conflict_length(paths), 200)
        self.assertEqual(parallel_conflict_length([[], [], [(0, 0)]]), 0)

    def test_invalid_clearance_is_rejected(self):
        for clearance in (-1, math.nan, math.inf, -math.inf, True, "8", 10 ** 400):
            with self.subTest(clearance=clearance), self.assertRaisesRegex(ValueError, "clearance"):
                parallel_conflict_length([], clearance)


if __name__ == "__main__":
    unittest.main()
