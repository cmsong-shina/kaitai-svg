from __future__ import annotations

from itertools import combinations, product
import math
import random
import re
import unicodedata
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.render import render_svg


SVG = "{http://www.w3.org/2000/svg}"


def intersects_interior(start, end, box) -> bool:
    """Detect axis-aligned segments entering the open rectangle of a node."""
    x1, y1 = start
    x2, y2 = end
    left, top = box.x, box.y
    right, bottom = left + box.width, top + box.height
    if math.isclose(x1, x2):
        return left < x1 < right and max(min(y1, y2), top) < min(max(y1, y2), bottom)
    if math.isclose(y1, y2):
        return top < y1 < bottom and max(min(x1, x2), left) < min(max(x1, x2), right)
    raise AssertionError(f"Route segment is not orthogonal: {start} -> {end}")


def class_style(xml, class_name: str) -> dict[str, str]:
    """Read the declarations affecting one exact class in the emitted SVG."""
    style = xml.find(f"{SVG}defs/{SVG}style").text
    result = {}
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
        if "." + class_name in [selector.strip() for selector in selectors.split(",")]:
            for declaration in declarations.split(";"):
                if ":" in declaration:
                    name, value = declaration.split(":", 1)
                    result[name.strip()] = value.strip()
    return result


def estimated_text_width(text: str, font_size: float, monospace: bool = False) -> float:
    """Independent width sanity bound; real-browser tests measure actual fonts."""
    em = sum(
        0 if unicodedata.combining(char) else 1.1 if unicodedata.east_asian_width(char) in "WF"
        else 1.05 if char == "W" and not monospace else 0.67
        for char in text
    )
    return em * font_size


class RenderTests(SchemaTestCase):
    def assert_layout_clear(self, graph):
        layout = layout_graph(graph)
        self.assertGreater(layout.width, 0)
        self.assertGreater(layout.height, 0)
        self.assertEqual(len(layout.nodes), len(graph.nodes))
        self.assertEqual(len(layout.edges), len(graph.edges))
        for box in layout.nodes:
            with self.subTest(node=box.node.id):
                self.assertGreater(box.width, 0)
                self.assertGreater(box.height, 0)
                self.assertGreaterEqual(box.x, 0)
                self.assertGreaterEqual(box.y, 0)
                self.assertLessEqual(box.x + box.width, layout.width)
                self.assertLessEqual(box.y + box.height, layout.height)
                self.assertEqual(len(box.rows), len(box.node.rows))
        for first, second in combinations(layout.nodes, 2):
            overlap = (
                first.x < second.x + second.width
                and second.x < first.x + first.width
                and first.y < second.y + second.height
                and second.y < first.y + first.height
            )
            self.assertFalse(overlap, f"Nodes overlap: {first.node.id}, {second.node.id}")
        for route in layout.edges:
            self.assertGreaterEqual(len(route.points), 2)
            for point in route.points:
                self.assertTrue(all(math.isfinite(value) for value in point))
                self.assertGreaterEqual(point[0], 0)
                self.assertGreaterEqual(point[1], 0)
                self.assertLessEqual(point[0], layout.width)
                self.assertLessEqual(point[1], layout.height)
            for box in layout.nodes:
                if box.node.id in (route.edge.source, route.edge.target):
                    continue
                for start, end in zip(route.points, route.points[1:]):
                    self.assertFalse(
                        intersects_interior(start, end, box),
                        f"Edge {route.edge.source} -> {route.edge.target} crosses {box.node.id}",
                    )

    def test_example_layout_has_no_overlaps_or_crossed_boxes(self):
        self.assert_layout_clear(load_graph(self.examples_dir / "packet.ksy"))

    def test_cycles_diamonds_self_references_and_disconnected_nodes(self):
        nodes = [
            Node(str(index), f"type_{index}", rows=[Row("field", "field", "u1")])
            for index in range(12)
        ]
        pairs = [(0, 1), (0, 2), (1, 3), (2, 3), (3, 0), (2, 2), (3, 4), (3, 5), (4, 6), (5, 6), (6, 1), (7, 8), (8, 9)]
        edges = [Edge(str(source), str(target), "field") for source, target in pairs]
        self.assert_layout_clear(Graph("Complex relationships", nodes, edges))

    def test_dense_graphs_with_varied_node_heights(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                randomizer = random.Random(seed)
                nodes = [
                    Node(str(index), f"node_{index}", rows=[
                        Row(f"row_{row}", f"field_{row}", "u1", details="condition " * (index % 5))
                        for row in range(index % 4 + 1)
                    ])
                    for index in range(20)
                ]
                edges = [
                    Edge(str(source), str(randomizer.randrange(20)), "row_0")
                    for source in range(20) for _ in range(3)
                ]
                self.assert_layout_clear(Graph("Dense graph", nodes, edges))

    def test_long_unicode_rows_stay_inside_their_nodes(self):
        node = Node(
            "root",
            "매우 긴 제목 " * 15,
            rows=[Row("long", "긴 필드 이름 " * 20, "some_really_long_nested_type_name" * 5, details="조건 설명 " * 20)],
        )
        layout = layout_graph(Graph("Long labels", [node], []))
        box = layout.nodes[0]
        self.assertEqual(len(box.rows), 1)
        row = box.rows[0]
        self.assertGreaterEqual(row.y, box.header_height)
        self.assertLessEqual(row.y + row.height, box.height)
        self.assert_layout_clear(Graph("Long labels", [node], []))

    def test_wide_latin_and_cjk_text_fit_declared_node_fonts(self):
        for content in ("W" * 120, "漢" * 80, "한글" * 70):
            with self.subTest(content=content[:8]):
                graph = Graph("Wide text", [Node(
                    "root", content, subtitle=content,
                    rows=[Row("field", content, content, details=content,
                              pos_label=content, size_label=content)],
                )], [])
                box = layout_graph(graph).nodes[0]
                xml = ET.fromstring(render_svg(graph))
                node_group = next(element for element in xml.iter(SVG + "g") if element.get("data-node-id") == "root")
                for class_name in ("node-title", "node-subtitle", "field-pos", "field-size", "field-label", "field-type", "field-detail"):
                    style = class_style(xml, class_name)
                    monospace = class_name != "node-subtitle"
                    self.assertIn("monospace" if monospace else "sans-serif", style.get("font-family", ""))
                    font_size = float(style["font-size"].removesuffix("px"))
                    lines = [element for element in node_group.iter(SVG + "text") if element.get("class") == class_name]
                    self.assertGreater(len(lines), 1)
                    self.assertEqual("".join(element.text or "" for element in lines), content)
                    for element in lines:
                        right_edge = float(element.get("x")) + estimated_text_width(element.text or "", font_size, monospace)
                        self.assertLessEqual(right_edge, box.width - 16, (class_name, element.text))
                for cell in (element for element in node_group.iter(SVG + "g")
                             if element.get("class") == "table-cell"):
                    for element in cell.iter(SVG + "text"):
                        style = class_style(xml, element.get("class"))
                        font_size = float(style["font-size"].removesuffix("px"))
                        self.assertGreaterEqual(float(element.get("x")), float(cell.get("data-cell-left")))
                        right_edge = float(element.get("x")) + estimated_text_width(element.text or "", font_size, True)
                        self.assertLessEqual(right_edge, float(cell.get("data-cell-right")),
                                             (cell.get("data-column"), element.text))

    def test_tables_group_sections_and_files_without_losing_field_metadata(self):
        self.schema("common.ksy", """
            meta: {id: common}
            seq:
              - {id: value, type: u2}
        """)
        source = self.schema("table.ksy", """
            meta: {id: table, imports: [common]}
            params:
              - {id: count, type: u1, doc: External parameter}
            seq:
              - {id: header, type: u1, doc: Header byte}
              - {id: body, type: common, doc: Imported body}
            instances:
              first_byte: {pos: 0, type: u1, doc: Read at absolute position}
              computed: {value: header + 1, doc: Calculated value}
        """)
        graph = load_graph(source)
        xml = ET.fromstring(render_svg(graph))
        nodes = {element.get("data-node-id"): element for element in xml.iter(SVG + "g")
                 if element.get("data-node-id") is not None}
        panels = [element for element in xml.iter(SVG + "g") if element.get("class") == "file-group"]
        self.assertEqual({panel.get("data-source-file") for panel in panels}, {"table.ksy", "common.ksy"})
        self.assertEqual(len(panels), 2)
        for model in graph.nodes:
            node = nodes[model.id]
            self.assertEqual(node.get("data-source-file"), model.source_file)
            self.assertEqual(node.get("data-size"), model.size_label)
            size_text = [element for element in node.iter(SVG + "text") if element.get("class") == "type-size"]
            self.assertTrue(size_text)
            self.assertIn(model.size_label, "".join(element.text or "" for element in size_text))
            for row in model.rows:
                group = next(element for element in node.iter(SVG + "g") if element.get("data-row-id") == row.id)
                self.assertEqual(group.get("data-pos"), row.pos_label)
                self.assertEqual(group.get("data-size"), row.size_label)
                cells = [element for element in group if element.get("class") == "table-cell"]
                self.assertEqual([cell.get("data-column") for cell in cells], ["pos", "size", "type", "id"])
                for cell, expected in zip(cells, (row.pos_label, row.size_label, row.type_label, row.label)):
                    self.assertEqual("".join(text.text or "" for text in cell.iter(SVG + "text")), expected)
                self.assertIn(row.doc, group.find(SVG + "title").text)
        root_model = next(node for node in graph.nodes if node.title == "table")
        root = nodes[root_model.id]
        sections = [element for element in root.iter(SVG + "g") if element.get("data-section-kind")]
        self.assertEqual([section.get("data-section-kind") for section in sections], ["params", "seq", "instances"])
        self.assertEqual([len([element for element in section.iter(SVG + "g") if element.get("data-row-id")])
                          for section in sections], [1, 2, 2])
        for section in sections:
            headings = [element.text for element in section.iter(SVG + "text")
                        if element.get("class") == "table-heading"]
            self.assertEqual(headings, ["pos", "size", "type", "id"])

    def test_enums_show_only_values_and_identifiers_without_byte_size(self):
        source = self.schema("enums.ksy", """
            meta: {id: enums}
            seq:
              - {id: status, type: u1, enum: state}
            enums:
              state:
                0: {id: idle, doc: No pending work}
                255: busy
        """)
        graph = load_graph(source)
        enum = next(node for node in graph.nodes if node.kind == "enum")
        xml = ET.fromstring(render_svg(graph))
        card = next(element for element in xml.iter(SVG + "g") if element.get("data-node-id") == enum.id)
        self.assertFalse(any(element.get("class") in {"size-badge", "type-size", "size-caption"}
                             for element in card.iter()))
        self.assertEqual([element.text for element in card.iter(SVG + "text")
                          if element.get("class") == "table-heading"], ["value", "id"])
        for row in enum.rows:
            group = next(element for element in card.iter(SVG + "g") if element.get("data-row-id") == row.id)
            cells = [element for element in group if element.get("class") == "table-cell"]
            self.assertEqual([cell.get("data-column") for cell in cells], ["type", "id"])
            self.assertEqual(["".join(text.text or "" for text in cell.iter(SVG + "text")) for cell in cells],
                             [row.type_label, row.label])
            self.assertIn(row.doc, group.find(SVG + "title").text)

    def test_import_and_nested_relationships_have_distinct_marker_semantics(self):
        xml = ET.fromstring(render_svg(load_graph(self.examples_dir / "packet.ksy"),
                                      show_imports=True, show_nested_types=True))
        markers = {element.get("id"): element for element in xml.iter(SVG + "marker")}
        imported = [element for element in xml.iter(SVG + "g")
                    if {"edge", "edge-kind-import"}.issubset(element.get("class", "").split())]
        nested = [element for element in xml.iter(SVG + "g")
                  if {"edge", "edge-kind-contains"}.issubset(element.get("class", "").split())]
        self.assertTrue(imported)
        self.assertTrue(nested)
        for groups, expected_start, expected_end in ((imported, None, "url(#arrow-import)"),
                                                     (nested, "url(#diamond-contains)", None)):
            for group in groups:
                path = next(element for element in group if "edge-path" in element.get("class", "").split())
                self.assertEqual(path.get("marker-start"), expected_start)
                self.assertEqual(path.get("marker-end"), expected_end)
        self.assertEqual(markers["arrow-import"].find(SVG + "path").get("fill"), "none")
        self.assertNotEqual(markers["diamond-contains"].find(SVG + "path").get("d"),
                            markers["arrow-import"].find(SVG + "path").get("d"))

    def test_single_node_diagram_title_and_captions_fit_canvas_header(self):
        for title, kind in product(("W" * 200, "한글" * 100), ("type", "enum")):
            with self.subTest(title=title[:8], kind=kind):
                graph = Graph(title, [Node("root", "root", kind=kind)], [])
                layout = layout_graph(graph)
                xml = ET.fromstring(render_svg(graph))
                visible_title = next(element for element in xml.iter(SVG + "text") if element.get("class") == "diagram-title")
                self.assertNotEqual(visible_title.text, title)
                self.assertEqual(xml.find(SVG + "title").text, title)
                captions = [element for element in xml.iter(SVG + "text") if element.get("class") == "diagram-caption"]
                self.assertGreaterEqual(len(captions), 2)
                header_lines = [visible_title, *captions]
                for element in header_lines:
                    style = class_style(xml, element.get("class"))
                    font_size = float(style["font-size"].removesuffix("px"))
                    right_edge = float(element.get("x")) + estimated_text_width(element.text or "", font_size)
                    self.assertLessEqual(right_edge, layout.width - 64)
                    self.assertLess(float(element.get("y")) + font_size * 0.3, layout.nodes[0].y)
                for previous, following in zip(header_lines, header_lines[1:]):
                    font_size = float(class_style(xml, following.get("class"))["font-size"].removesuffix("px"))
                    self.assertGreaterEqual(float(following.get("y")) - float(previous.get("y")), font_size)

    def test_docs_are_escaped_and_attached_to_hover_targets(self):
        doc = '문서 <tag> & "quotes" </script><script>alert(1)</script>'
        graph = Graph(
            "Example <diagram> & documentation",
            [Node("node<&", "Root <&>", doc=doc, rows=[Row("row<&", "field<&", "u1", doc=doc)])],
            [],
        )
        xml = ET.fromstring(render_svg(graph))
        self.assertEqual(xml.tag, SVG + "svg")
        self.assertIsNotNone(xml.get("viewBox"))
        node_group = next(element for element in xml.iter(SVG + "g") if element.get("data-node-id") == "node<&")
        row_group = next(element for element in xml.iter(SVG + "g") if element.get("data-row-id") == "row<&")
        for element in (node_group, row_group):
            title = element.find(SVG + "title")
            self.assertIsNotNone(title)
            self.assertIn(doc, title.text)
        # Input documentation must remain inert text, including apparent tags.
        scripts = list(xml.iter(SVG + "script"))
        self.assertFalse(any(script.text == "alert(1)" for script in scripts))

    def test_svg_contains_all_nodes_and_relationships(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        xml = ET.fromstring(render_svg(graph, show_imports=True, show_nested_types=True))
        node_ids = {element.get("data-node-id") for element in xml.iter(SVG + "g") if element.get("data-node-id") is not None}
        self.assertEqual(node_ids, {node.id for node in graph.nodes})
        paths = [element for element in xml.iter(SVG + "path") if "edge-path" in element.get("class", "").split()]
        self.assertEqual(len(paths), len(graph.edges))
        for edge in graph.edges:
            self.assertTrue(any(path.get("data-source") == edge.source and path.get("data-target") == edge.target for path in paths))

    def test_default_render_omits_import_and_nested_connections_before_layout(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        original_edges = list(graph.edges)
        filtered = Graph(graph.title, graph.nodes,
                         [edge for edge in graph.edges if edge.kind not in {"import", "contains"}])
        self.assertLess(len(filtered.edges), len(original_edges))
        default_svg = render_svg(graph)
        self.assertEqual(default_svg, render_svg(filtered))
        self.assertEqual(graph.edges, original_edges)
        xml = ET.fromstring(default_svg)
        self.assertFalse(any("edge-kind-import" in g.get("class", "").split()
                             for g in xml.iter(SVG + "g")))
        self.assertFalse(any("edge-kind-contains" in g.get("class", "").split()
                             for g in xml.iter(SVG + "g")))
        self.assertNotIn("show imports", xml.find(SVG + "desc").text)
        self.assertNotIn("show nested definitions", xml.find(SVG + "desc").text)
        node_ids = {g.get("data-node-id") for g in xml.iter(SVG + "g")
                    if g.get("data-node-id") is not None}
        self.assertEqual(node_ids, {node.id for node in graph.nodes})
        paths = [p for p in xml.iter(SVG + "path") if p.get("class") == "edge-path"]
        self.assertEqual(len(paths), len(filtered.edges))
        captions = [t.text for t in xml.iter(SVG + "text") if t.get("class") == "diagram-caption"]
        self.assertIn(f"{len(graph.nodes)} definitions · {len(filtered.edges)} connections", captions)
        for edge in filtered.edges:
            self.assertTrue(any(p.get("data-source") == edge.source and p.get("data-target") == edge.target
                                for p in paths))

    def test_import_and_nested_options_are_independent(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        for interactive, show_imports, show_nested_types in product((False, True), repeat=3):
            with self.subTest(interactive=interactive, imports=show_imports, nested=show_nested_types):
                xml = ET.fromstring(render_svg(graph, show_imports=show_imports,
                                              show_nested_types=show_nested_types,
                                              interactive=interactive))
                self.assertEqual(xml.find(SVG + "script") is not None, interactive)
                for kind, enabled, description in (("import", show_imports, "show imports"),
                                                   ("contains", show_nested_types, "show nested definitions")):
                    groups = [g for g in xml.iter(SVG + "g")
                              if f"edge-kind-{kind}" in g.get("class", "").split()]
                    expected = sum(edge.kind == kind for edge in graph.edges) + 1  # legend
                    self.assertEqual(len(groups), expected if enabled else 0)
                    self.assertEqual(description in xml.find(SVG + "desc").text, enabled)
                node_ids = {g.get("data-node-id") for g in xml.iter(SVG + "g")
                            if g.get("data-node-id") is not None}
                self.assertEqual(node_ids, {node.id for node in graph.nodes})

    def test_render_is_deterministic(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        self.assertEqual(render_svg(graph), render_svg(graph))


if __name__ == "__main__":
    unittest.main()
