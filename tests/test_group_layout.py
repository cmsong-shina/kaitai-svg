"""Table sections and file panels must keep their geometry independently clear."""

from itertools import combinations
from types import SimpleNamespace
import unittest

from kaitai_svg.layout import layout_graph, monospace_width, wrap_monospace
from kaitai_svg.model import Edge, Graph, Node, Row


def overlaps(first, second):
    return (first.x < second.x + second.width and second.x < first.x + first.width
            and first.y < second.y + second.height and second.y < first.y + first.height)


def enters(start, end, box):
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        return box.x < x1 < box.x + box.width and max(min(y1, y2), box.y) < min(max(y1, y2), box.y + box.height)
    if y1 == y2:
        return box.y < y1 < box.y + box.height and max(min(x1, x2), box.x) < min(max(x1, x2), box.x + box.width)
    raise AssertionError("Connector is not orthogonal")


class GroupLayoutTests(unittest.TestCase):
    def test_four_cells_wrap_inside_columns_and_details_follow_tallest_cell(self):
        row = Row("field", "한글_" * 30, "very_long_type_" * 18, details="condition " * 80,
                  pos_label="offset_expression_" * 8, size_label="length_expression_" * 8)
        box = layout_graph(Graph("Table", [Node("root", "root", rows=[row])], [])).nodes[0]
        self.assertEqual([column.key for column in box.columns], ["pos", "size", "type", "id"])
        self.assertGreaterEqual(box.width, 560)
        self.assertLessEqual(box.width, 720)
        self.assertEqual(sum(column.width for column in box.columns), box.width)
        measured = box.rows[0]
        cell_lines = [measured.pos_lines, measured.size_lines, measured.type_lines, measured.label_lines]
        for column, lines in zip(box.columns, cell_lines):
            font_size = 13 if column.key == "id" else 12
            self.assertGreater(len(lines), 1)
            for line in lines:
                self.assertLessEqual(monospace_width(line) * font_size * .67, column.width - 20)
        self.assertGreater(measured.detail_y, 18 + 16 * (max(map(len, cell_lines)) - 1))
        self.assertLess(measured.detail_y + 13 * (len(measured.detail_lines) - 1), measured.height)

    def test_monospace_wrap_keeps_narrow_identifiers_and_cjk_inside_their_cells(self):
        text = "iiii....||||" * 20 + "한글" * 10
        lines = wrap_monospace(text, 20)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(monospace_width(line) <= 20 for line in lines))
        self.assertEqual(monospace_width("iiii....||||"), 12)
        self.assertEqual(monospace_width("한글"), 4)

    def test_enum_only_measures_value_and_id_without_binary_size(self):
        enum = Node("mode", "mode", kind="enum", size_label="unknown", rows=[
            Row("0", "disabled", "0", kind="enum"),
            Row("1", "enabled", "1", kind="enum"),
        ])
        regular = Node("root", "root", size_label="1 B", rows=[Row("mode", "mode", "u1")])
        enum_box, type_box = layout_graph(Graph("Compact", [enum, regular], [])).nodes
        self.assertEqual([(column.key, column.label) for column in enum_box.columns], [("type", "value"), ("id", "id")])
        self.assertEqual(enum_box.size_lines, [])
        self.assertTrue(all(not row.pos_lines and not row.size_lines for row in enum_box.rows))
        self.assertGreaterEqual(enum_box.width, 320)
        self.assertLessEqual(enum_box.width, 440)
        self.assertLess(enum_box.width, type_box.width)
        self.assertEqual([row.height for row in enum_box.rows], [26, 26])
        self.assertEqual(type_box.rows[0].height, 26)
        enum_only = layout_graph(Graph("Enum only", [enum], []))
        self.assertEqual(enum_only.nodes[0].width, enum_box.width)
        self.assertGreaterEqual(enum_only.width, 680)

    def test_params_seq_instances_get_distinct_bands_even_after_port_expansion(self):
        source = Node("source", "source", rows=[
            Row("instance", "computed", "value", kind="instance"),
            Row("seq", "payload", "target"),
            Row("param", "length", "u4", kind="param"),
        ])
        graph = Graph("Sections", [source, Node("target", "target")],
                      [Edge("source", "target", "seq") for _ in range(12)])
        box = layout_graph(graph).nodes[0]
        self.assertEqual([section.kind for section in box.sections], ["params", "seq", "instances"])
        self.assertEqual([row.row.id for row in box.rows], ["param", "seq", "instance"])
        cursor = box.header_height
        for section in box.sections:
            self.assertEqual(section.y, cursor)
            row_cursor = section.y + section.header_height + section.table_header_height
            for row in section.rows:
                self.assertEqual(row.y, row_cursor)
                row_cursor += row.height
            self.assertEqual(row_cursor, section.y + section.height)
            cursor += section.height
        self.assertEqual(cursor, box.height)

    def test_file_panels_contain_nodes_without_overlap_and_keep_routes_clear(self):
        nodes = [Node(str(index), f"file::type_{index}", source_file="nested/" * 30 + f"folder_{index // 4}/same.ksy", rows=[
            Row("field", "payload", "nested", details="rule " * (index % 3))
            for _ in range(1)
        ]) for index in range(12)]
        edges = [Edge(str(index), str((index + 1) % 12), "field") for index in range(12)]
        edges += [Edge("0", "9", "field"), Edge("7", "2", "field"), Edge("5", "5", "field")]
        graph = Graph("Files", nodes, edges)
        layout = layout_graph(graph)
        self.assertEqual(len(layout.groups), 3)
        for first, second in combinations(layout.groups, 2):
            self.assertFalse(overlaps(first, second))
        by_id = {box.node.id: box for box in layout.nodes}
        for group in layout.groups:
            self.assertGreater(len(group.title_lines), 1)
            for node_id in group.node_ids:
                box = by_id[node_id]
                self.assertEqual(box.node.source_file, group.source_file)
                self.assertGreaterEqual(box.x, group.x + 24)
                self.assertGreaterEqual(box.y, group.y + group.header_height)
                self.assertLessEqual(box.x + box.width, group.x + group.width - 24)
                self.assertLessEqual(box.y + box.height, group.y + group.height - 24)
        for first, second in combinations(layout.nodes, 2):
            self.assertFalse(overlaps(first, second))
        self.assertEqual(len(layout.edges), len(edges))
        for route in layout.edges:
            for start, end in zip(route.points, route.points[1:]):
                for box in layout.nodes:
                    self.assertFalse(enters(start, end, box), (route.edge, box.node.id))
                for group in layout.groups:
                    # Covers the first-line count and every wrapped filename
                    # line, independently from the router's clearance bounds.
                    caption = SimpleNamespace(x=group.x + 24, y=group.y + 10,
                                              width=group.width - 48, height=group.header_height - 18)
                    self.assertFalse(enters(start, end, caption), (route.edge, group.source_file))
        self.assertEqual(layout, layout_graph(graph))

    def test_large_enum_file_uses_three_columns_and_long_file_title_has_room(self):
        source_file = "nested/" * 20 + "shared_enums.ksy"
        nodes = [Node(str(index), f"enums::enum_{index}", source_file=source_file, kind="enum", rows=[
            Row(str(value), f"value_{value}", str(value), kind="enum") for value in range(index + 1)
        ]) for index in range(9)]
        layout = layout_graph(Graph("Enums", nodes, []))
        group = layout.groups[0]
        self.assertEqual(len({box.x for box in layout.nodes}), 3)
        self.assertEqual("".join(group.title_lines), source_file)
        self.assertGreaterEqual(min(box.y for box in layout.nodes), group.y + group.header_height)
        self.assertTrue(all(box.title_lines[0].startswith("enum_") for box in layout.nodes))

    def test_nodes_without_file_metadata_keep_unframed_compatibility(self):
        layout = layout_graph(Graph("Legacy", [Node("root", "root")], []))
        self.assertEqual(layout.groups, [])
        self.assertEqual(layout.nodes[0].node.title, "root")


if __name__ == "__main__":
    unittest.main()
