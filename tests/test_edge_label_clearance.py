"""Opt-in edge labels avoid route halos without changing legacy rendering."""

import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from kaitai_svg.layout import EdgeLayout, FileGroupLayout, Layout, _measure
from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.render import _EdgeLabelContext, _render_edge, render_svg


def horizontal(label="10", y=200):
    return EdgeLayout(Edge("a", "b", label=label), [(50, y), (68, y), (232, y), (250, y)])


def label_element(svg):
    return ET.fromstring(svg).find("text[@class='edge-label']")


class EdgeLabelClearanceTests(unittest.TestCase):
    def test_label_moves_below_adjacent_parallel_route(self):
        item = horizontal()
        neighbor = EdgeLayout(Edge("c", "d"), [(30, 192), (270, 192)])
        context = _EdgeLabelContext(Layout([], [item, neighbor], 300, 400), 0)
        rendered = _render_edge(item, dict(a="A", b="B"), interactive=True, label_context=context)
        text = label_element(rendered)
        self.assertEqual(text.text, "10")
        self.assertGreater(float(text.get("y")), 200)
        self.assertTrue(all(context._overlap(context.labels[0], route) == 0 for route in context.routes))

    def test_label_uses_another_position_when_card_blocks_midpoint(self):
        item = horizontal()
        neighbor = EdgeLayout(Edge("c", "d"), [(30, 192), (270, 192)])
        card = _measure(Node("card", "card"))
        card.x, card.y, card.width, card.height = 112, 204, 76, 40
        context = _EdgeLabelContext(Layout([card], [item, neighbor], 300, 400), 0)
        text = label_element(_render_edge(item, dict(a="A", b="B"), interactive=False, label_context=context))
        self.assertNotEqual(float(text.get("x")), 150)
        self.assertTrue(all(context._overlap(context.labels[0], obstacle) == 0
                            for obstacle in context.obstacles))

    def test_prior_labels_and_file_headers_are_obstacles(self):
        first, second = horizontal("one"), horizontal("two")
        group = FileGroupLayout("a.ksy", "a.ksy", 20, 150, 260, 300, [], ["a.ksy"], 40)
        context = _EdgeLabelContext(Layout([], [first, second], 300, 500, [group]), 0)
        for item in (first, second):
            _render_edge(item, dict(a="A", b="B"), interactive=False, label_context=context)
        self.assertEqual(len(context.labels), 2)
        self.assertEqual(context._overlap(*context.labels), 0)
        self.assertTrue(all(context._overlap(box, obstacle) == 0 for box in context.labels
                            for obstacle in context.obstacles))

    def test_vertical_label_can_move_to_other_side_without_changing_rotation(self):
        item = EdgeLayout(Edge("a", "b", label="case"),
                          [(132, 150), (150, 150), (150, 350), (168, 350)])
        neighbor = EdgeLayout(Edge("c", "d"), [(142, 140), (142, 360)])
        context = _EdgeLabelContext(Layout([], [item, neighbor], 300, 400), 0)
        text = label_element(_render_edge(item, dict(a="A", b="B"), interactive=False, label_context=context))
        self.assertIn("rotate(-90)", text.get("transform"))
        self.assertGreater(context.labels[0][0], 150)
        self.assertTrue(all(context._overlap(context.labels[0], route) == 0 for route in context.routes))

    def test_fallback_stays_inside_canvas_and_preserves_display_text_and_tooltip(self):
        item = horizontal("long switch expression that must wrap or truncate")
        card = _measure(Node("card", "card"))
        card.x, card.y, card.width, card.height = 0, 114, 300, 286
        context = _EdgeLabelContext(Layout([card], [item], 300, 400), 0)
        legacy = _render_edge(item, dict(a="A", b="B"), interactive=True)
        revised = _render_edge(item, dict(a="A", b="B"), interactive=True, label_context=context)
        self.assertEqual(label_element(revised).text, label_element(legacy).text)
        self.assertEqual(ET.fromstring(revised).find("title").text,
                         ET.fromstring(legacy).find("title").text)
        box, bounds = context.labels[0], context.bounds
        self.assertTrue(bounds[0] <= box[0] <= box[2] <= bounds[2])
        self.assertTrue(bounds[1] <= box[1] <= box[3] <= bounds[3])

    def test_default_context_preserves_existing_label_position_and_svg(self):
        item = horizontal()
        legacy = _render_edge(item, dict(a="A", b="B"), interactive=True)
        self.assertEqual(legacy, _render_edge(item, dict(a="A", b="B"), interactive=True,
                                             label_context=None))
        self.assertEqual(label_element(legacy).get("y"), "194")
        graph = Graph("Legacy", [Node("a", "A", rows=[Row("field", "field", "b")]),
                                 Node("b", "B")], [Edge("a", "b", "field", "case")])
        with patch("kaitai_svg.render._EdgeLabelContext", side_effect=AssertionError("legacy label context")):
            for layout in ("default", "optimized", "vertical-optimized"):
                with self.subTest(layout=layout):
                    self.assertIn('class="edge-label"', render_svg(graph, layout=layout))


if __name__ == "__main__":
    unittest.main()
