from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.render import render_svg


SVG = "{http://www.w3.org/2000/svg}"


def interaction_graph() -> Graph:
    return Graph(
        "Interactive example",
        [
            Node("root", "Root", doc="Root paragraph one.\n\nRoot paragraph two.", rows=[
                Row("field", "payload", "body", doc='First line.\n\nSecond line:\t<script>alert("inert")</script> & text.'),
                Row("footer", "footer", "other", doc="Another field's type."),
            ]),
            Node("body", "Body", doc="Connected type documentation."),
            Node("other", "Other", doc="Another field's type documentation."),
        ],
        [Edge("root", "body", "field"), Edge("root", "other", "footer")],
    )


class InteractionTests(unittest.TestCase):
    def test_static_default_has_native_docs_without_interactive_behavior(self):
        graph = interaction_graph()
        xml = ET.fromstring(render_svg(graph))
        self.assertEqual(render_svg(graph), render_svg(graph, interactive=False))
        self.assertFalse(list(xml.iter(SVG + "script")))
        self.assertFalse(list(xml.iter(SVG + "foreignObject")))
        for element in xml.iter():
            self.assertNotIn(element.get("id"), {"controls", "tooltip"})
            self.assertIsNone(element.get("tabindex"))
            self.assertIsNone(element.get("data-tooltip"))
            self.assertFalse(any(name.lower().startswith("on") for name in element.attrib))
        _, _, width, height = map(float, xml.get("viewBox").split())
        self.assertEqual(float(xml.get("width")), width)
        self.assertEqual(float(xml.get("height")), height)
        style = xml.find(f"{SVG}defs/{SVG}style").text
        for interactive_rule in (":hover", ":focus", ".active", ".muted",
                                 "touch-action: none", "user-select: none"):
            self.assertNotIn(interactive_rule, style)
        root = next(el for el in xml.iter(SVG + "g") if el.get("data-node-id") == "root")
        row = next(el for el in root.iter(SVG + "g") if el.get("data-row-id") == "field")
        self.assertEqual(root.find(SVG + "title").text, "Root\n\n" + graph.nodes[0].doc)
        self.assertEqual(row.find(SVG + "title").text,
                         "payload\n\nbody\n\n" + graph.nodes[0].rows[0].doc)
        description = xml.find(SVG + "desc").text
        captions = " ".join(el.text or "" for el in xml.iter(SVG + "text")
                            if el.get("class") == "diagram-caption")
        for instruction in ("Scroll to zoom", "drag to pan", "press 0 to reset"):
            self.assertNotIn(instruction, description)
        for instruction in ("Hover/Tab", "Wheel: zoom", "Drag: pan"):
            self.assertNotIn(instruction, captions)

    def test_interactive_opt_in_preserves_geometry_and_can_be_disabled_again(self):
        graph = interaction_graph()
        static_svg = render_svg(graph)
        static = ET.fromstring(static_svg)
        interactive = ET.fromstring(render_svg(graph, interactive=True))
        self.assertEqual(interactive.get("width"), "100%")
        self.assertEqual(interactive.get("height"), "100%")
        self.assertEqual(interactive.get("viewBox"), static.get("viewBox"))
        self.assertIsNotNone(interactive.find(SVG + "script"))
        self.assertTrue(list(interactive.iter(SVG + "foreignObject")))
        self.assertTrue(any(el.get("id") == "controls" for el in interactive.iter()))
        self.assertTrue(any(el.get("id") == "tooltip" for el in interactive.iter()))
        self.assertTrue(any(el.get("tabindex") == "0" for el in interactive.iter()))
        self.assertTrue(any(el.get("data-tooltip") for el in interactive.iter()))
        style = interactive.find(f"{SVG}defs/{SVG}style").text
        for interactive_rule in (":hover", ":focus", ".active", ".muted",
                                 "touch-action: none", "user-select: none"):
            self.assertIn(interactive_rule, style)
        shape_classes = {"canvas-bg", "node-box", "node-header", "node-accent",
                         "row-divider", "column-divider", "edge-path", "edge-halo", "port"}
        def shapes(xml):
            return [(el.tag, dict(el.attrib)) for el in xml.iter()
                    if el.get("class") in shape_classes]
        self.assertEqual(shapes(static), shapes(interactive))
        for tag in ("marker", "filter"):
            self.assertEqual([ET.tostring(el) for el in static.iter(SVG + tag)],
                             [ET.tostring(el) for el in interactive.iter(SVG + tag)])
        self.assertEqual(render_svg(graph, interactive=False), static_svg)

    def test_tooltip_attribute_preserves_doc_paragraphs_and_tabs(self):
        graph = interaction_graph()
        xml = ET.fromstring(render_svg(graph, interactive=True))
        root = next(el for el in xml.iter(SVG + "g") if el.get("data-node-id") == "root")
        row = next(el for el in root.iter(SVG + "g") if el.get("data-row-id") == "field")
        self.assertEqual(root.get("data-tooltip"), "Root\n\n" + graph.nodes[0].doc)
        self.assertEqual(row.get("data-tooltip"), "payload\n\nbody\n\n" + graph.nodes[0].rows[0].doc)
        self.assertEqual(row.find(SVG + "title").text, row.get("data-tooltip"))

    @unittest.skipUnless(shutil.which("node"), "Node.js is optional and is needed for script interaction checks")
    def test_embedded_script_interactions(self):
        xml = ET.fromstring(render_svg(interaction_graph(), interactive=True))
        nodes = []
        for element in xml.iter(SVG + "g"):
            if element.get("data-node-id") is None:
                continue
            rows = [
                {"id": row.get("data-row-id"), "tooltip": row.get("data-tooltip")}
                for row in element.iter(SVG + "g") if row.get("data-row-id") is not None
            ]
            nodes.append({"id": element.get("data-node-id"), "tooltip": element.get("data-tooltip"), "rows": rows})
        edges = [
            {"source": edge.get("data-source"), "target": edge.get("data-target"),
             "sourceRow": edge.get("data-source-row"), "tooltip": edge.get("data-tooltip")}
            for edge in xml.iter(SVG + "g") if "edge" in edge.get("class", "").split()
        ]
        fixture = {"script": xml.find(SVG + "script").text, "nodes": nodes, "edges": edges}
        result = subprocess.run(
            [shutil.which("node"), str(Path(__file__).with_name("interaction.cjs"))],
            input=json.dumps(fixture), text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
