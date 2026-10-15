"""Spatial routing demand and local port alignment drive adaptive spacing."""

from copy import deepcopy
import unittest

from kaitai_svg.layout import _measure, layout_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.placement import OptimizedWithSpaceLayout
from tests import test_optimized_layout as geometry_checks


def measured_cards(count, row_counts=None, *, source_file=""):
    return [_measure(Node(str(i), f"type_{i}", source_file=source_file,
                          rows=[Row(str(r), f"field_{r}", "u1")
                                for r in range(row_counts[i] if row_counts else 1)]))
            for i in range(count)]


class SpaceSpacingTests(unittest.TestCase):
    assert_clear_geometry = geometry_checks.OptimizedLayoutTests.assert_clear_geometry

    def test_disjoint_vertical_flows_reuse_gutter_tracks(self):
        nodes = measured_cards(6)
        panels = [("", [[0, 1, 2], [3, 4, 5]])]
        engine = OptimizedWithSpaceLayout()
        disjoint = engine._pack_space(panels, nodes, [(0, 3, "0"), (1, 4, "0"), (2, 5, "0")], 1, False)
        overlapping = engine._pack_space(panels, nodes, [(0, 5, "0"), (1, 4, "0"), (2, 3, "0")], 1, False)
        disjoint_gap = disjoint.positions[3][0] - disjoint.positions[0][0] - nodes[0].width
        overlapping_gap = overlapping.positions[3][0] - overlapping.positions[0][0] - nodes[0].width
        self.assertEqual(disjoint_gap, 72)
        self.assertGreater(overlapping_gap, disjoint_gap)
        self.assertLess(overlapping_gap, 360)

    def test_long_reference_does_not_reserve_vertical_tracks_in_every_gutter(self):
        nodes = measured_cards(4)
        engine = OptimizedWithSpaceLayout()
        drawing = engine._pack_space([("", [[0], [1], [2], [3]])], nodes,
                                     [(0, 3, "0")] * 12, 1, False)
        gaps = [drawing.positions[i + 1][0] - drawing.positions[i][0] - nodes[i].width
                for i in range(3)]
        self.assertGreater(gaps[0], 72)
        self.assertEqual(gaps[1:], [72, 72])

    def test_overlapping_same_column_and_self_references_reserve_side_tracks(self):
        nodes = measured_cards(3)
        panels = [("", [[0, 1], [2]])]
        engine = OptimizedWithSpaceLayout()
        for edges in ([(0, 1, "0"), (1, 0, "0")], [(0, 0, "0")] * 8):
            with self.subTest(edges=edges):
                drawing = engine._pack_space(panels, nodes, edges, 1, False)
                gap = drawing.positions[2][0] - drawing.positions[0][0] - nodes[0].width
                self.assertGreater(gap, 72)
                self.assertLessEqual(gap, 360)

    def test_unrelated_import_and_cross_column_edges_leave_card_gaps_compact(self):
        nodes = measured_cards(3, source_file="a.ksy")
        engine = OptimizedWithSpaceLayout()
        for panels in (
            [("a.ksy", [[0, 1]]), ("b.ksy", [[2]])],
            [("a.ksy", [[0, 1], [2]])],
        ):
            with self.subTest(panels=panels):
                drawing = engine._pack_space(panels, nodes, [(0, 2, "0")] * 24, 1, False)
                gap = drawing.positions[1][1] - drawing.positions[0][1] - nodes[0].height
                self.assertEqual(gap, 40)

    def test_long_same_column_reference_uses_only_endpoint_detour_gaps(self):
        nodes = measured_cards(4)
        drawing = OptimizedWithSpaceLayout()._pack_space([("", [[0, 1, 2, 3]])], nodes,
                                                        [(0, 3, "0")] * 8, 1, False)
        gaps = [drawing.positions[i + 1][1] - drawing.positions[i][1] - nodes[i].height
                for i in range(3)]
        self.assertGreater(gaps[0], 40)
        self.assertEqual(gaps[1], 40)
        self.assertGreater(gaps[2], 40)
        self.assertTrue(all(40 <= gap <= 120 for gap in gaps))

    def test_long_import_does_not_expand_intermediate_panel_gap(self):
        nodes = measured_cards(4, source_file="types.ksy")
        panels = [(f"file_{i}.ksy", [[i]]) for i in range(4)]
        drawing = OptimizedWithSpaceLayout()._pack_space(panels, nodes, [(0, 3, "0")] * 8, 1, False)
        gaps = [drawing.groups[i + 1].y - drawing.groups[i].y - drawing.groups[i].height
                for i in range(3)]
        self.assertGreater(gaps[0], 64)
        self.assertEqual(gaps[1], 64)
        self.assertGreater(gaps[2], 64)

    def test_individual_card_alignment_adjusts_gaps_without_reordering(self):
        nodes = measured_cards(4, (3, 1, 2, 1), source_file="types.ksy")
        panels = [("types.ksy", [[0, 1], [2, 3]])]
        edges = [(0, 2, "2"), (1, 3, "0")]
        original = deepcopy(nodes)
        engine = OptimizedWithSpaceLayout()
        compact = engine._pack_space(panels, nodes, edges, 1, False)
        aligned = engine._pack_space(panels, nodes, edges, 1, True)

        def mismatch(candidate):
            return sum(abs(candidate.positions[a][1] + engine._row_offset(nodes[a], row)
                           - candidate.positions[b][1] - nodes[b].header_height / 2)
                       for a, b, row in edges)

        self.assertLess(mismatch(aligned), mismatch(compact) / 2)
        gaps = [aligned.positions[lower][1] - aligned.positions[upper][1] - nodes[upper].height
                for upper, lower in ((0, 1), (2, 3))]
        self.assertTrue(any(gap > 40 for gap in gaps))
        self.assertTrue(all(40 <= gap <= 120 for gap in gaps))
        self.assertTrue(all(y >= aligned.groups[0].y + aligned.groups[0].header_height
                            for _, y in aligned.positions))
        self.assertEqual(nodes, original)

    def test_spacing_candidates_change_congested_tracks_and_preserve_minima(self):
        nodes = measured_cards(6)
        panels = [("", [[0, 1, 2], [3, 4, 5]])]
        edges = [(0, 5, "0"), (1, 4, "0"), (2, 3, "0")]
        engine = OptimizedWithSpaceLayout()
        gaps = []
        for spacing in (0.7, 1.0, 1.35):
            drawing = engine._pack_space(panels, nodes, edges, 1, False, spacing)
            gaps.append(drawing.positions[3][0] - drawing.positions[0][0] - nodes[0].width)
        self.assertLess(gaps[0], gaps[1])
        self.assertLess(gaps[1], gaps[2])
        self.assertTrue(all(72 <= gap <= 360 for gap in gaps))

    def test_spatial_candidates_keep_complete_drawings_clear(self):
        nodes = [box.node for box in measured_cards(6, (3, 1, 2, 1, 1, 2), source_file="types.ksy")]
        graph = Graph("References", nodes, [Edge(str(a), str(b), row) for a, b, row in
                      [(0, 2, "2"), (1, 3, "0"), (2, 4, "1"), (4, 0, "0"), (5, 5, "0")]])
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                drawing = layout_graph(graph, layout="optimized-with-space", router=router,
                                       layout_width=2000)
                self.assertLessEqual(drawing.width, 2000)
                self.assert_clear_geometry(drawing)


if __name__ == "__main__":
    unittest.main()
