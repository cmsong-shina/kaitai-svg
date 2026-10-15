from contextlib import redirect_stdout
from io import StringIO
from itertools import combinations, product
from math import dist
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import convert
from kaitai_svg.cli import main
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.palette import EDGE_BACKGROUNDS, TYPE_EDGE_COLORS, _contrast, _oklab
from kaitai_svg.render import render_svg


SVG = "{http://www.w3.org/2000/svg}"


def reference_graph(count):
    rows = [Row(f"field_{index:02}", f"field_{index:02}", "body") for index in range(count)]
    return Graph("Type connections", [Node("root", "root", rows=rows), Node("body", "body")],
                 [Edge("root", "body", row.id, row.label) for row in rows])


def edge_groups(xml):
    return [group for group in xml.iter(SVG + "g") if "edge" in group.get("class", "").split()]


def type_markers(xml):
    return {(group.get("data-source"), group.get("data-source-row"), group.get("data-target")):
            group.find(f'{SVG}path[@class="edge-path"]').get("marker-end")
            for group in edge_groups(xml) if "edge-kind-type" in group.get("class", "").split()}


class TypeEdgeColorTests(SchemaTestCase):
    def test_palette_has_twelve_perceptually_separated_high_contrast_colors(self):
        self.assertEqual(len(set(TYPE_EDGE_COLORS)), 12)
        for color, background in product(TYPE_EDGE_COLORS, EDGE_BACKGROUNDS):
            with self.subTest(color=color, background=background):
                self.assertGreaterEqual(_contrast(color, background), 3.5)
        for first, second in combinations(TYPE_EDGE_COLORS, 2):
            self.assertGreaterEqual(dist(_oklab(first), _oklab(second)), .13, (first, second))

    def test_each_connection_is_unique_up_to_twelve_then_colors_repeat(self):
        for count in (0, 1, 12, 13, 25):
            with self.subTest(count=count):
                graph = reference_graph(count)
                xml = ET.fromstring(render_svg(graph, color_type_edges=True))
                markers = list(type_markers(xml).values())
                self.assertEqual(len(markers), count)
                self.assertEqual(len(set(markers)), min(count, 12))
                self.assertEqual(len(set(markers[:12])), min(count, 12))
                if count > 12:
                    self.assertEqual(markers[0], markers[12])
                self.assertEqual(render_svg(graph), render_svg(graph, color_type_edges=False))
                for index, color in enumerate(TYPE_EDGE_COLORS[:min(count, 12)]):
                    arrow = xml.find(f'{SVG}defs/{SVG}marker[@id="arrow-type-{index}"]/{SVG}path')
                    self.assertEqual(arrow.get("fill"), color)
                    style = xml.find(f"{SVG}defs/{SVG}style").text
                    self.assertIn(f".type-edge-color-{index} .edge-path {{ stroke: {color}; }}", style)
                    self.assertIn(f".type-edge-color-{index} .port {{ fill: {color}; }}", style)

    def test_colors_survive_input_reordering_and_other_display_options(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        default = ET.fromstring(render_svg(graph, color_type_edges=True))
        expected = type_markers(default)
        self.assertTrue(expected)
        for imports, nested, interactive in product((False, True), repeat=3):
            with self.subTest(imports=imports, nested=nested, interactive=interactive):
                options = dict(show_imports=imports, show_nested_types=nested, interactive=interactive)
                plain = ET.fromstring(render_svg(graph, **options))
                colored = ET.fromstring(render_svg(graph, color_type_edges=True, **options))
                self.assertEqual(type_markers(colored), expected)
                def other_edges(xml):
                    return [ET.tostring(group) for group in edge_groups(xml)
                            if "edge-kind-type" not in group.get("class", "").split()]
                self.assertEqual(other_edges(plain), other_edges(colored))
        reordered = Graph(graph.title, graph.nodes, list(reversed(graph.edges)))
        self.assertEqual(type_markers(ET.fromstring(render_svg(reordered, color_type_edges=True))), expected)

    def test_cli_and_public_api_enable_and_disable_colors(self):
        root = self.schema("colors.ksy", """
            meta: {id: colors}
            seq:
              - {id: first, type: body}
              - {id: second, type: body}
            types:
              body:
                seq:
                  - {id: value, type: u1}
        """)
        for api, interactive in product((False, True), repeat=2):
            for enabled in (False, True, False):
                with self.subTest(api=api, interactive=interactive, enabled=enabled):
                    if api:
                        convert(root, color_type_edges=enabled, interactive=interactive)
                    else:
                        flags = ["--color-type-edges"] if enabled else []
                        if interactive:
                            flags.append("--interactive")
                        with redirect_stdout(StringIO()):
                            self.assertEqual(main([str(root), *flags]), 0)
                    xml = ET.parse(root.with_suffix(".svg")).getroot()
                    markers = set(type_markers(xml).values())
                    self.assertEqual(len(markers), 2 if enabled else 1)
                    self.assertEqual(xml.find(SVG + "script") is not None, interactive)
                    if not enabled:
                        self.assertEqual(markers, {"url(#arrow)"})


if __name__ == "__main__":
    unittest.main()
