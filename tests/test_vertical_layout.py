"""Web-width placement contracts, independent of the placement implementation."""

from copy import deepcopy
from itertools import combinations
import math
from types import SimpleNamespace
import unittest

from kaitai_svg.layout import layout_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.placement import LayoutEngine, get_layout_engine
from tests.test_router import enters_rectangle, segments


def overlaps(first, second):
    return (first.x < second.x + second.width and second.x < first.x + first.width
            and first.y < second.y + second.height and second.y < first.y + first.height)


def wide_nodes(count=12, *, grouped=False):
    return [Node(str(index), f"type_{index}",
                 source_file=f"file_{index // 4}.ksy" if grouped else "",
                 rows=[Row("payload", "payload_identifier_" * 5, "long_type_expression_" * 6,
                           pos_label="position_expression_" * 6,
                           size_label="size_expression_" * 6)])
            for index in range(count)]


def enum_nodes(count=8, source_file=""):
    return [Node(str(index), f"enum_{index}", kind="enum", source_file=source_file,
                 rows=[Row("0", "disabled", "0", kind="enum"),
                       Row("1", "enabled", "1", kind="enum")])
            for index in range(count)]


class VerticalLayoutTests(unittest.TestCase):
    def assert_clear_geometry(self, drawing, width=1200):
        self.assertGreater(drawing.width, 0)
        self.assertLessEqual(drawing.width, width)
        self.assertGreater(drawing.height, 0)
        for first, second in combinations(drawing.nodes, 2):
            self.assertFalse(overlaps(first, second), (first.node.id, second.node.id))
        for first, second in combinations(drawing.groups, 2):
            self.assertFalse(overlaps(first, second), (first.source_file, second.source_file))
        captions = [SimpleNamespace(x=group.x + 24, y=group.y + 10,
                                    width=group.width - 48, height=group.header_height - 18)
                    for group in drawing.groups]
        by_id = {box.node.id: box for box in drawing.nodes}
        for box in drawing.nodes:
            self.assertGreaterEqual(box.x, 0)
            self.assertGreaterEqual(box.y, 114)
            self.assertLessEqual(box.x + box.width, drawing.width)
            self.assertLessEqual(box.y + box.height, drawing.height)
        for group in drawing.groups:
            self.assertLessEqual(group.x + group.width, drawing.width)
            for node_id in group.node_ids:
                box = by_id[node_id]
                self.assertGreaterEqual(box.x, group.x + 24)
                self.assertGreaterEqual(box.y, group.y + group.header_height)
                self.assertLessEqual(box.x + box.width, group.x + group.width - 24)
                self.assertLessEqual(box.y + box.height, group.y + group.height - 24)
        for route in drawing.edges:
            source, target = by_id[route.edge.source], by_id[route.edge.target]
            self.assertEqual(route.points[0][0], source.x + source.width)
            self.assertEqual(route.points[-1][0], target.x)
            if route.edge.source_row is None:
                low, high = source.y, source.y + source.header_height
            else:
                row = next(row for row in source.rows if row.row.id == route.edge.source_row)
                low, high = source.y + row.y, source.y + row.y + row.height
            self.assertLessEqual(low, route.points[0][1])
            self.assertLessEqual(route.points[0][1], high)
            self.assertLessEqual(target.y, route.points[-1][1])
            self.assertLessEqual(route.points[-1][1], target.y + target.header_height)
            for x, y in route.points:
                self.assertTrue(math.isfinite(x) and math.isfinite(y))
                self.assertTrue(0 <= x <= drawing.width and 0 <= y <= drawing.height, (x, y))
            for a, b in segments(route.points):
                self.assertTrue(a[0] == b[0] or a[1] == b[1], (a, b))
                for box in [*drawing.nodes, *captions]:
                    self.assertFalse(enters_rectangle(a, b, box), (route.edge, box))

    def test_registered_engine_and_default_width(self):
        engine = get_layout_engine("vertical-optimized")
        self.assertTrue(issubclass(engine, LayoutEngine))
        graph = Graph("A web diagram", wide_nodes(), [])
        drawing = layout_graph(graph, layout="vertical-optimized")
        self.assertEqual(drawing, layout_graph(graph, layout="vertical-optimized", layout_width=None))
        self.assertEqual(drawing, layout_graph(graph, layout=engine, layout_width=1200))
        self.assert_clear_geometry(drawing)

    def test_edgeless_wide_cards_wrap_instead_of_reusing_wide_default(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                graph = Graph("No connectors", wide_nodes(grouped=grouped), [])
                baseline = layout_graph(graph)
                drawing = layout_graph(graph, layout="vertical-optimized")
                if grouped:
                    self.assertGreater(baseline.width, 1200)
                    self.assertGreater(drawing.height, baseline.height)
                self.assertEqual([box.node for box in drawing.nodes], graph.nodes)
                self.assertEqual(len(drawing.groups), 3 if grouped else 0)
                self.assert_clear_geometry(drawing)

    def test_routed_large_graph_stays_inside_web_width_with_both_routers(self):
        for grouped in (False, True):
            nodes = wide_nodes(grouped=grouped)
            edges = [Edge(str(index), str(index + 1), "payload") for index in range(11)]
            edges.extend([Edge("11", "0", "payload"), Edge("2", "9", "payload"),
                          Edge("6", "1", "payload"), Edge("5", "5", "payload")])
            graph = Graph("Connected cards", nodes, edges)
            for router in ("default", "orthogonal"):
                with self.subTest(grouped=grouped, router=router):
                    drawing = layout_graph(graph, layout="vertical-optimized", router=router)
                    self.assertEqual([route.edge for route in drawing.edges], edges)
                    self.assert_clear_geometry(drawing)

    def test_width_override_changes_column_capacity_without_resizing_cards(self):
        graph = Graph("Compact enums", enum_nodes(), [])
        narrow = layout_graph(graph, layout="vertical-optimized", layout_width=960)
        wide = layout_graph(graph, layout="vertical-optimized", layout_width=1440)
        self.assert_clear_geometry(narrow, 960)
        self.assert_clear_geometry(wide, 1440)
        self.assertGreater(len({box.x for box in wide.nodes}), len({box.x for box in narrow.nodes}))
        self.assertLess(wide.height, narrow.height)
        self.assertEqual([(box.width, box.height) for box in narrow.nodes],
                         [(box.width, box.height) for box in wide.nodes])

    def test_compact_enums_share_columns_and_wide_cards_keep_their_readable_width(self):
        enums = enum_nodes(source_file="compact.ksy")
        compact = layout_graph(Graph("Small cards", enums, []), layout="vertical-optimized")
        self.assertGreaterEqual(len({box.x for box in compact.nodes}), 2)
        self.assert_clear_geometry(compact)
        graph = Graph("Mixed sizes", [*wide_nodes(3, grouped=True),
                                      *[Node("enum_" + node.id, node.title, kind=node.kind,
                                             source_file="compact.ksy", rows=node.rows)
                                        for node in enums]], [])
        original_sizes = {box.node.id: (box.width, box.height)
                          for box in layout_graph(graph).nodes}
        drawing = layout_graph(graph, layout="vertical-optimized")
        self.assertEqual({box.node.id: (box.width, box.height) for box in drawing.nodes}, original_sizes)
        self.assert_clear_geometry(drawing)

    def test_wrapped_group_titles_root_first_and_semantic_rows_survive_optimization(self):
        files = ["deep_directory/" * 20 + f"source_{index}.ksy" for index in range(3)]
        nodes = [Node(str(index), f"source::type_{index}", source_file=files[index // 2], rows=[
            Row("computed", "computed", "value", kind="instance"),
            Row("field", "field", "target"),
            Row("length", "length", "u4", kind="param"),
        ]) for index in range(6)]
        edges = [Edge(str(a), str(b), "field") for a, b in
                 [(0, 4), (1, 1), (2, 0), (3, 5), (4, 3), (5, 4)]]
        graph = Graph("Groups", nodes, edges, root_source_file=files[1])
        before = deepcopy(graph)
        drawing = layout_graph(graph, layout="vertical-optimized", router="orthogonal")
        self.assertEqual(graph, before)
        self.assertEqual(drawing, layout_graph(graph, layout="vertical-optimized", router="orthogonal"))
        self.assertEqual([box.node for box in drawing.nodes], nodes)
        self.assertEqual([route.edge for route in drawing.edges], edges)
        self.assertEqual(min(drawing.groups, key=lambda group: group.y).source_file, files[1])
        self.assertEqual(len({group.x for group in drawing.groups}), 1)
        for group in drawing.groups:
            self.assertGreater(len(group.title_lines), 1)
            self.assertEqual("".join(group.title_lines), group.source_file)
            self.assertEqual(set(group.node_ids), {node.id for node in nodes
                                                   if node.source_file == group.source_file})
        legacy = layout_graph(graph)
        for box, original in zip(drawing.nodes, legacy.nodes):
            self.assertEqual((box.width, box.height, box.header_height, box.rows),
                             (original.width, original.height, original.header_height, original.rows))
            self.assertEqual([row.row.id for row in box.rows], ["length", "field", "computed"])
        self.assert_clear_geometry(drawing)

    def test_empty_and_mixed_metadata_graphs_use_the_width_contract(self):
        empty = layout_graph(Graph("Empty", [], []), layout="vertical-optimized", layout_width=960)
        self.assertEqual((empty.nodes, empty.edges, empty.groups), ([], [], []))
        self.assertLessEqual(empty.width, 960)
        nodes = [Node("a", "a", source_file="sample.ksy"), Node("b", "b")]
        drawing = layout_graph(Graph("Mixed", nodes, [Edge("a", "b"), Edge("b", "a")]),
                               layout="vertical-optimized", router="orthogonal")
        self.assertEqual([box.node for box in drawing.nodes], nodes)
        self.assert_clear_geometry(drawing)

    def test_invalid_widths_are_rejected_for_nonempty_and_empty_graphs(self):
        for graph in (Graph("Cards", [Node("a", "a")], []), Graph("Empty", [], [])):
            for width in (0, -1, math.nan, math.inf, -math.inf, 10 ** 400, "1200", True):
                with self.subTest(width=width, empty=not graph.nodes):
                    with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                        layout_graph(graph, layout="vertical-optimized", layout_width=width)

    def test_impossibly_narrow_width_raises_instead_of_shrinking_or_overflowing(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                graph = Graph("Readable card", wide_nodes(1, grouped=grouped), [])
                with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                    layout_graph(graph, layout="vertical-optimized", layout_width=300)

    def test_minimum_width_boundary_keeps_readable_cards_and_caption_space(self):
        for source_file in ("", "sample.ksy"):
            with self.subTest(source_file=source_file):
                graph = Graph("One readable card", [Node("card", "card", source_file=source_file)], [])
                normal = layout_graph(graph, layout="vertical-optimized")
                tight = layout_graph(graph, layout="vertical-optimized", layout_width=normal.width)
                self.assertEqual(tight, normal)
                self.assert_clear_geometry(tight, normal.width)
                with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                    layout_graph(graph, layout="vertical-optimized", layout_width=normal.width - 1)
        empty = Graph("Empty caption", [], [])
        drawing = layout_graph(empty, layout="vertical-optimized", layout_width=680)
        self.assertEqual(drawing.width, 680)
        with self.assertRaisesRegex(ValueError, "[Ww]idth"):
            layout_graph(empty, layout="vertical-optimized", layout_width=679)

    def test_width_is_rejected_for_layouts_that_do_not_support_it(self):
        for layout in ("default", "optimized"):
            for graph in (Graph("Card", [Node("a", "a")], []), Graph("Empty", [], [])):
                with self.subTest(layout=layout, empty=not graph.nodes):
                    with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                        layout_graph(graph, layout=layout, layout_width=1200)


if __name__ == "__main__":
    unittest.main()
