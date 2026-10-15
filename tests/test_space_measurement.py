"""Reflow, typography, and restoration checks for candidate-owned measurements."""

import unittest

from kaitai_svg.layout import (_measure, _measure_adaptive, _reserve_port_space,
                               _text_height, layout_graph, monospace_width)
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.geometry import PathMetrics
from kaitai_svg.placement import DefaultLayout, LayoutEngine, OptimizedWithSpaceLayout, Placement
from kaitai_svg.render import render_svg


class SpaceMeasurementTests(unittest.TestCase):
    def test_space_is_spent_on_the_column_responsible_for_tall_rows(self):
        for long_column in ("id", "type"):
            with self.subTest(column=long_column):
                node = Node("sample", "sample", rows=[Row(
                    "field", "identifier" * 16 if long_column == "id" else "moderate_identifier",
                    "nested_type" * 16 if long_column == "type" else "reasonably_long_type",
                )])
                natural = _measure(node)
                adaptive = _measure_adaptive(node, natural.width)
                columns = {column.key: column.width for column in adaptive.columns}
                old_columns = {column.key: column.width for column in natural.columns}
                self.assertGreater(columns[long_column], old_columns[long_column])
                self.assertLess(adaptive.height, natural.height)
                self.assertEqual(adaptive.width, natural.width)

    def test_width_changes_reflow_and_preserves_original_rows_and_section_aliases(self):
        node = Node("sample", "sample", rows=[
            Row("p", "count", "u4", kind="param"),
            Row("f", "long_identifier" * 10, "nested_type" * 12),
            Row("i", "computed", "expression", kind="instance"),
        ])
        narrow = _measure_adaptive(node, 454)
        wide = _measure_adaptive(node, 900)
        self.assertGreater(narrow.height, wide.height)
        self.assertGreater(_text_height([narrow]), _text_height([wide]))
        for measured in (narrow, wide):
            self.assertIs(measured.node, node)
            self.assertEqual([row.row for row in measured.rows], node.rows)
            self.assertEqual(sum(column.width for column in measured.columns), measured.width)
            for row in measured.rows:
                self.assertIs(next(item for section in measured.sections for item in section.rows
                                   if item.row.id == row.row.id), row)
                self.assertLessEqual(row.y + row.height, measured.height)

    def test_unicode_cells_fit_at_extreme_widths_without_font_changes(self):
        for kind, widths in (("type", (454, 900)), ("enum", (272, 550))):
            for content in ("WWWW" * 30, "한글漢字" * 30, "e\u0301" * 80):
                for width in widths:
                    with self.subTest(kind=kind, content=content[:6], width=width):
                        node = Node("sample", content, subtitle=content, kind=kind,
                                    rows=[Row("f", content, content, details=content,
                                              pos_label=content, size_label=content,
                                              kind="enum" if kind == "enum" else "field")])
                        measured = _measure_adaptive(node, width)
                        row = measured.rows[0]
                        lines = {"pos": row.pos_lines, "size": row.size_lines,
                                 "type": row.type_lines, "id": row.label_lines}
                        for column in measured.columns:
                            self.assertEqual("".join(lines[column.key]), content)
                            font = 13 if column.key == "id" else 12
                            padding = 26 if column.key in ("id", "pos") or (
                                kind == "enum" and column.key == "type") else 20
                            for line in lines[column.key]:
                                self.assertLessEqual(monospace_width(line) * font * .67,
                                                     column.width - padding + 1e-8)
                        self.assertLessEqual(row.y + row.height, measured.height)
        compact = _measure_adaptive(Node("enum", "enum", kind="enum"), 550)
        self.assertLessEqual(compact.columns[0].width, 112)

    def test_earlier_winner_restores_its_full_reflow_geometry_and_routes(self):
        class NarrowOnly(LayoutEngine):
            targets = (454,)

            def placements(self, graph, nodes):
                candidates = []
                for width in self.targets:
                    measured = [_measure_adaptive(node, width) for node in graph.nodes]
                    _reserve_port_space(measured, graph)
                    placement = DefaultLayout().placements(graph, measured)[0]
                    candidates.append(Placement(placement.positions, placement.groups, measured))
                return candidates

            def score_placement(self, metrics, width, height, placement):
                return (placement.nodes[0].width,)

        class EarlierWins(NarrowOnly):
            targets = (454, 900)

        graph = Graph("Reflow restoration", [
            Node("a", "a", source_file="sample.ksy", rows=[
                Row("p", "count", "u1", kind="param"),
                Row("f", "field_name" * 8, "type_expression" * 12),
            ]),
            Node("b", "b", source_file="sample.ksy"),
        ], [Edge("a", "b", "f") for _ in range(8)])
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                expected = layout_graph(graph, layout=NarrowOnly, router=router)
                actual = layout_graph(graph, layout=EarlierWins, router=router)
                self.assertEqual(actual, expected)
                self.assertEqual(render_svg(graph, layout=EarlierWins, router=router),
                                 render_svg(graph, layout=NarrowOnly, router=router))
                self.assertGreaterEqual(actual.nodes[0].rows[1].height, 24 + 18 * 7)

    def test_candidate_measurements_cannot_replace_the_input_nodes(self):
        class ReplacedNode(LayoutEngine):
            def placements(self, graph, nodes):
                replacement = _measure(Node("a", "a"))
                return [Placement([(64, 132)], [], [replacement])]

        with self.assertRaisesRegex(ValueError, "identity"):
            layout_graph(Graph("Invalid replacement", [Node("a", "a")], []),
                         layout=ReplacedNode)

    def test_balanced_cost_allows_crossings_for_space_but_prioritizes_shared_lines(self):
        graph = Graph("Balanced selection", [Node("a", "a"), Node("b", "b")], [Edge("a", "b")])
        natural = [_measure(node) for node in graph.nodes]
        _reserve_port_space(natural, graph)
        engine = OptimizedWithSpaceLayout()
        candidate = engine.placements(graph, natural)[0]
        compact = engine.score_placement(PathMetrics(0, 1, 6, 1000), 800, 500, candidate)
        spacious = engine.score_placement(PathMetrics(0, 0, 6, 1000), 1600, 1000, candidate)
        hidden_line = engine.score_placement(PathMetrics(1, 0, 0, 10), 680, 200, candidate)
        self.assertLess(compact, spacious)
        self.assertLess(spacious, hidden_line)

    def test_busy_structure_routes_multiple_gaps_with_the_same_card_measurements(self):
        graph = Graph("Routing space", [
            Node("a", "a", rows=[Row("value", "value", "u1")]), Node("b", "b"),
        ], [Edge("a", "b", "value") for _ in range(12)])
        measured = [_measure(node) for node in graph.nodes]
        _reserve_port_space(measured, graph)
        candidates = OptimizedWithSpaceLayout().placements(graph, measured)
        gaps_by_geometry = {}
        for candidate in candidates:
            source, target = candidate.positions
            if target[0] <= source[0]:
                continue
            signature = (tuple(node.width for node in candidate.nodes), source[1], target[1])
            gap = target[0] - source[0] - candidate.nodes[0].width
            gaps_by_geometry.setdefault(signature, set()).add(gap)
        self.assertTrue(any(len(gaps) >= 2 for gaps in gaps_by_geometry.values()))
        self.assertLessEqual(len(candidates), 6)


if __name__ == "__main__":
    unittest.main()
