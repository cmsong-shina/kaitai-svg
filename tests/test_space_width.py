"""Explicit width budgets distribute cards across the available canvas."""

from pathlib import Path
import unittest
from unittest.mock import patch

from kaitai_svg.layout import _measure, _reserve_port_space, _route_edges, layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Graph, Node, Row
from kaitai_svg.placement import OptimizedWithSpaceLayout
from tests import test_optimized_layout as geometry_checks


def many_types(count=24):
    return Graph("Many short types", [
        Node(str(index), f"type_{index}", source_file="many.ksy",
             rows=[Row("value", "value", "u1")])
        for index in range(count)
    ], [])


def many_files(count=12):
    return Graph("Many source files", [
        Node(str(index), f"type_{index}", source_file=f"file_{index}.ksy",
             rows=[Row("value", "value", "u1")])
        for index in range(count)
    ], [])


def column_count(drawing, source_file):
    return len({box.x for box in drawing.nodes if box.node.source_file == source_file})


class SpaceWidthTests(unittest.TestCase):
    assert_clear_geometry = geometry_checks.OptimizedLayoutTests.assert_clear_geometry

    def test_wider_budget_distributes_short_types_beyond_four_columns(self):
        graph = many_types()
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                narrow = layout_graph(graph, layout="optimized-with-space",
                                      layout_width=1200, router=router)
                wide = layout_graph(graph, layout="optimized-with-space",
                                    layout_width=4000, router=router)
                self.assertLessEqual(narrow.width, 1200)
                self.assertLessEqual(wide.width, 4000)
                self.assertGreater(column_count(wide, "many.ksy"), 4)
                self.assertGreater(column_count(wide, "many.ksy"),
                                   column_count(narrow, "many.ksy"))
                self.assertLess(wide.height, narrow.height)
                self.assert_clear_geometry(narrow)
                self.assert_clear_geometry(wide)

    def test_wider_budget_distributes_source_file_groups_beyond_two_lanes(self):
        graph = many_files()
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                narrow = layout_graph(graph, layout="optimized-with-space",
                                      layout_width=1200, router=router)
                wide = layout_graph(graph, layout="optimized-with-space",
                                    layout_width=4000, router=router)
                self.assertLessEqual(narrow.width, 1200)
                self.assertLessEqual(wide.width, 4000)
                self.assertEqual(len(narrow.groups), len(graph.nodes))
                self.assertEqual(len(wide.groups), len(graph.nodes))
                self.assertLessEqual(len({group.x for group in narrow.groups}), 2)
                self.assertGreater(len({group.x for group in wide.groups}), 2)
                self.assertLess(wide.height, narrow.height)
                self.assert_clear_geometry(narrow)
                self.assert_clear_geometry(wide)

    def test_sqlite_wider_budget_uses_more_than_two_columns_and_reduces_height(self):
        source = Path(__file__).resolve().parents[1] / "examples" / "sqlite3.ksy"
        loaded = load_graph(source)
        # Match render_svg's default visibility without writing the example SVG.
        graph = Graph(loaded.title, loaded.nodes,
                      [edge for edge in loaded.edges if edge.kind not in {"import", "contains"}],
                      loaded.root_source_file)
        for router in ("default", "orthogonal"):
            with self.subTest(router=router):
                narrow = layout_graph(graph, layout="optimized-with-space",
                                      layout_width=1200, router=router)
                wide = layout_graph(graph, layout="optimized-with-space",
                                    layout_width=4000, router=router)
                self.assertLessEqual(narrow.width, 1200)
                self.assertLessEqual(wide.width, 4000)
                self.assertGreater(column_count(wide, graph.root_source_file), 2)
                self.assertLess(wide.height, narrow.height)
                self.assert_clear_geometry(narrow)
                self.assert_clear_geometry(wide)

    def test_wide_shortlist_retains_a_candidate_beyond_four_columns(self):
        graph = many_types()
        measured = [_measure(node) for node in graph.nodes]
        _reserve_port_space(measured, graph)
        engine = OptimizedWithSpaceLayout()
        engine.set_width_limit(4000)
        with patch.object(engine, "_space_proxy", wraps=engine._space_proxy) as proxy:
            candidates = engine.placements(graph, measured)
        self.assertLessEqual(proxy.call_count, 64)
        self.assertLessEqual(len(candidates), 6)
        self.assertTrue(any(len({x for x, _ in candidate.positions}) > 4
                            for candidate in candidates))
        for candidate in candidates:
            width, _ = engine._canvas(candidate, candidate.nodes)
            self.assertLessEqual(width, 4000)

    def test_search_and_routing_remain_bounded_with_many_admissible_columns(self):
        class RecordingLayout(OptimizedWithSpaceLayout):
            proxy_calls = 0

            def _space_proxy(self, *args):
                type(self).proxy_calls += 1
                return super()._space_proxy(*args)

        for graph in (many_types(48), many_files(24)):
            for router in ("default", "orthogonal"):
                with self.subTest(graph=graph.title, router=router):
                    RecordingLayout.proxy_calls = 0
                    with patch("kaitai_svg.layout._route_edges", wraps=_route_edges) as route:
                        drawing = layout_graph(graph, layout=RecordingLayout,
                                               layout_width=12000, router=router)
                    self.assertGreater(RecordingLayout.proxy_calls, 0)
                    self.assertLessEqual(RecordingLayout.proxy_calls, 64)
                    self.assertGreater(route.call_count, 0)
                    self.assertLessEqual(route.call_count, 6)
                    self.assertLessEqual(drawing.width, 12000)
                    if graph.title == "Many short types":
                        self.assertGreater(column_count(drawing, "many.ksy"), 4)
                    else:
                        self.assertGreater(len({group.x for group in drawing.groups}), 4)
                    self.assert_clear_geometry(drawing)


if __name__ == "__main__":
    unittest.main()
