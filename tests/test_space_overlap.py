"""Colored connectors must remain visible after hiding the SQLite import."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from itertools import combinations
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from kaitai_svg.cli import convert_file, main
from kaitai_svg.geometry import measure_paths, path_segments
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Graph
from kaitai_svg.render import render_svg
from kaitai_svg.visibility import filter_hidden
from tests import test_optimized_layout as geometry_checks


ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"
OPTIONS = dict(interactive=True, color_type_edges=True, router="orthogonal",
               layout="optimized-with-space", hide=("vlq_base128_be.ksy",), layout_width=2000)


def svg_paths(svg: str) -> list[list[tuple[float, float]]]:
    """Read the painted paths, excluding their duplicated hit/halo geometry."""
    result = []
    for path in ET.fromstring(svg).iter(SVG + "path"):
        if path.get("class") != "edge-path":
            continue
        values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", path.get("d"), re.I)]
        result.append(list(zip(values[::2], values[1::2])))
    return result


def parallel_paint_conflicts(paths, clearance: float = 4.5) -> list[dict]:
    """Find long parallel runs whose colored strokes a later halo can erase.

    Each SVG edge paints a 6px background halo before its 1.8px foreground;
    keyboard/pointer highlighting makes that foreground 3px. Distinct parallel
    centerlines therefore need at least (6 + 3) / 2 pixels of separation.
    Perpendicular crossings retain the intended halo crossing treatment.
    """
    segments = [path_segments(path) for path in paths]
    conflicts = []
    for first, second in combinations(range(len(paths)), 2):
        for a, b in segments[first]:
            for c, d in segments[second]:
                axis = (0 if a[1] == b[1] and c[1] == d[1] else
                        1 if a[0] == b[0] and c[0] == d[0] else None)
                if axis is None:
                    continue
                distance = abs(a[1 - axis] - c[1 - axis])
                length = max(0, min(max(a[axis], b[axis]), max(c[axis], d[axis]))
                             - max(min(a[axis], b[axis]), min(c[axis], d[axis])))
                if distance < clearance - 1e-8 and length > 1e-8:
                    conflicts.append(dict(edges=(first, second), distance=distance,
                                          length=length, first=(a, b), second=(c, d)))
    return conflicts


class SpaceOverlapTests(unittest.TestCase):
    assert_clear_geometry = geometry_checks.OptimizedLayoutTests.assert_clear_geometry

    @classmethod
    def setUpClass(cls):
        cls.source = ROOT / "examples" / "sqlite3.ksy"
        cls.loaded = load_graph(cls.source)
        cls.original = deepcopy(cls.loaded)
        hidden = filter_hidden(cls.loaded, OPTIONS["hide"])
        cls.graph = Graph(hidden.title, hidden.nodes,
                          [edge for edge in hidden.edges if edge.kind not in {"import", "contains"}],
                          hidden.root_source_file)
        cls.drawing = layout_graph(cls.graph, router="orthogonal", layout="optimized-with-space",
                                   layout_width=2000)
        cls.svg = render_svg(cls.loaded, **OPTIONS)

    def test_exact_hidden_import_layout_has_no_shared_or_erased_connectors(self):
        drawing = self.drawing
        self.assertEqual((len(drawing.nodes), len(drawing.edges)), (13, 16))
        self.assertLessEqual(drawing.width, 2000)
        self.assertEqual(self.loaded, self.original)
        self.assertTrue(all(box.node is node for box, node in zip(drawing.nodes, self.graph.nodes)))
        self.assertTrue(all(box.node.source_file != "vlq_base128_be.ksy" for box in drawing.nodes))
        self.assert_clear_geometry(drawing)
        paths = [route.points for route in drawing.edges]
        self.assertEqual(measure_paths(paths).shared_length, 0)
        self.assertEqual(parallel_paint_conflicts(paths), [],
                         "Parallel connectors must clear the other edge's painted halo")
        self.assertEqual(parallel_paint_conflicts(paths, 8), [],
                         "Adaptive routing must preserve its physical 8px track clearance")

    def test_exact_interactive_colored_svg_preserves_options_and_distinct_visible_paths(self):
        root = ET.fromstring(self.svg)
        nodes = [item for item in root.iter(SVG + "g") if "node" in item.get("class", "").split()]
        edges = [item for item in root.iter(SVG + "g") if "edge" in item.get("class", "").split()]
        self.assertEqual((len(nodes), len(edges)), (13, 16))
        self.assertIsNotNone(root.find(SVG + "script"))
        self.assertIsNotNone(root.find(f".//{SVG}g[@id='controls']"))
        self.assertEqual((root.get("width"), root.get("height")), ("100%", "100%"))
        self.assertLessEqual(float(root.get("viewBox").split()[2]), 2000)
        self.assertFalse(any(item.get("data-source-file") == "vlq_base128_be.ksy"
                             for item in root.iter(SVG + "g")))
        type_edges = [edge for edge in edges if "edge-kind-type" in edge.get("class", "").split()]
        self.assertTrue(type_edges)
        self.assertTrue(all("type-edge-color-" in edge.get("class", "") for edge in type_edges))
        paths = svg_paths(self.svg)
        self.assertEqual(measure_paths(paths).shared_length, 0)
        self.assertEqual(parallel_paint_conflicts(paths), [],
                         "Rendered rounding and edge halos must preserve connector visibility")
        self.assertEqual(parallel_paint_conflicts(paths, 8), [],
                         "Rendered routes must preserve the selected 8px clearance")

    def test_exact_cli_and_conversion_api_write_only_to_requested_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            api = convert_file(self.source, out_dir=output / "api", **OPTIONS)
            self.assertEqual(api, output / "api" / "sqlite3.svg")
            self.assertEqual(api.read_text(encoding="utf-8"), self.svg)
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main([str(self.source), "--interactive", "--color-type-edges",
                               "--router", "orthogonal", "--layout", "optimized-with-space",
                               "--hide", "vlq_base128_be.ksy", "--layout-width", "2000",
                               "--out-dir", str(output / "cli")])
            self.assertEqual(status, 0, stderr.getvalue())
            cli = output / "cli" / "sqlite3.svg"
            self.assertIn(str(cli), stdout.getvalue())
            self.assertEqual(cli.read_text(encoding="utf-8"), self.svg)


class SpacePortApproachTests(unittest.TestCase):
    """The wider SQLite layout must not bounce around a fixed port anchor."""

    assert_clear_geometry = geometry_checks.OptimizedLayoutTests.assert_clear_geometry

    @classmethod
    def setUpClass(cls):
        cls.loaded = load_graph(ROOT / "examples" / "sqlite3.ksy")
        hidden = filter_hidden(cls.loaded, OPTIONS["hide"])
        cls.graph = Graph(hidden.title, hidden.nodes,
                          [edge for edge in hidden.edges if edge.kind not in {"import", "contains"}],
                          hidden.root_source_file)
        cls.drawing = layout_graph(cls.graph, router="orthogonal", layout="optimized-with-space",
                                   layout_width=4000)
        cls.svg = render_svg(cls.loaded, **{**OPTIONS, "layout_width": 4000})

    def test_wide_layout_removes_terminal_bounces_without_losing_clearance(self):
        drawing = self.drawing
        self.assertEqual((len(drawing.nodes), len(drawing.edges)), (13, 16))
        self.assertLessEqual(drawing.width, 4000)
        self.assert_clear_geometry(drawing)
        paths = [edge.points for edge in drawing.edges]
        self.assertEqual(measure_paths(paths).shared_length, 0)
        self.assertEqual(parallel_paint_conflicts(paths, 8), [])
        self.assertLessEqual(measure_paths(paths).crossings, 2)
        titles = {node.node.id: node.node.title.split("::")[-1] for node in drawing.nodes}
        affected = [edge for edge in drawing.edges
                    if titles[edge.edge.target] in {"column_content", "ref_cell"}]
        self.assertEqual(len(affected), 2)
        for edge in affected:
            with self.subTest(target=titles[edge.edge.target]):
                self.assertLessEqual(measure_paths([edge.points]).bends, 4,
                                     "The fixed routing anchor must not add two terminal turns")

    def test_wide_interactive_svg_has_clean_arrows_at_both_affected_targets(self):
        root = ET.fromstring(self.svg)
        self.assertIsNotNone(root.find(SVG + "script"))
        target_ids = {node.node.id for node in self.drawing.nodes
                      if node.node.title.split("::")[-1] in {"column_content", "ref_cell"}}
        edge_paths = [item for item in root.iter(SVG + "path")
                      if item.get("class") == "edge-path" and item.get("data-target") in target_ids]
        self.assertEqual(len(edge_paths), 2)
        for path in edge_paths:
            values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path.get("d"))]
            points = list(zip(values[::2], values[1::2]))
            self.assertLessEqual(measure_paths([points]).bends, 4)
            self.assertTrue(path.get("marker-end", "").startswith("url(#arrow-type-"))
        self.assertEqual(parallel_paint_conflicts(svg_paths(self.svg), 8), [])


if __name__ == "__main__":
    unittest.main()
