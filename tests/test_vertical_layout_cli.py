from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import LayoutEngine, VerticalOptimizedLayout, convert
from kaitai_svg.cli import convert_file, main
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.placement import LAYOUTS
from kaitai_svg.render import render_svg
from kaitai_svg.routing import ROUTERS


SVG = "{http://www.w3.org/2000/svg}"


class VerticalLayoutCliTests(SchemaTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.schema("packet.ksy", """
            meta: {id: packet, imports: [common]}
            seq:
              - {id: first, type: body, doc: First body}
              - {id: second, type: body}
              - {id: shared, type: common::payload}
            types:
              body:
                seq:
                  - {id: value, type: u1}
        """)
        self.schema("common.ksy", """
            meta: {id: common}
            types:
              payload:
                seq:
                  - {id: value, type: u2}
              unused:
                seq:
                  - {id: extra, type: u1}
        """)
        self.graph = load_graph(self.source)
        self.output = self.source.with_suffix(".svg")

    def invoke(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_vertical_class_is_public_and_registered(self):
        self.assertIs(LAYOUTS["vertical-optimized"], VerticalOptimizedLayout)
        self.assertTrue(issubclass(VerticalOptimizedLayout, LayoutEngine))
        self.assertTrue(VerticalOptimizedLayout.web_vertical)

    def test_cli_vertical_layout_composes_with_each_router_and_width(self):
        for router in ROUTERS:
            with self.subTest(router=router):
                status, stdout, errors = self.invoke(
                    str(self.source), "--layout", "vertical-optimized",
                    "--router", router, "--layout-width", "1440",
                )
                self.assertEqual(status, 0, errors)
                self.assertIn(str(self.output), stdout)
                self.assertEqual(self.output.read_text(encoding="utf-8"), render_svg(
                    self.graph, layout="vertical-optimized", router=router, layout_width=1440,
                ))

    def test_width_reaches_layout_graph_through_both_conversion_apis(self):
        for entrypoint in (convert_file, convert):
            for selected in ("vertical-optimized", VerticalOptimizedLayout):
                with self.subTest(entrypoint=entrypoint.__name__, layout=selected):
                    with mock.patch("kaitai_svg.render.layout_graph", wraps=layout_graph) as builder:
                        entrypoint(self.source, layout=selected, layout_width=1440.5)
                    self.assertEqual(builder.call_count, 1)
                    self.assertIs(builder.call_args.kwargs["layout"], selected)
                    self.assertEqual(builder.call_args.kwargs["layout_width"], 1440.5)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), render_svg(
                        self.graph, layout=selected, layout_width=1440.5,
                    ))

    def test_invalid_cli_width_is_rejected_without_conversion_or_writes(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        for width in ("0", "-1", "1.5", "nan", "infinity", "wide"):
            with self.subTest(width=width):
                stderr = StringIO()
                with redirect_stderr(stderr), mock.patch("kaitai_svg.cli.convert_file") as converter:
                    with self.assertRaises(SystemExit) as raised:
                        main([str(self.source), "--layout", "vertical-optimized",
                              "--layout-width", width])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("positive integer", stderr.getvalue())
                converter.assert_not_called()
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
                self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_invalid_api_width_preserves_output_and_uncreated_output_directory(self):
        self.output.write_text("previous output", encoding="utf-8")
        out_dir = self.tmp_path / "new-output"
        for width in (0, -1, float("inf"), float("nan")):
            for entrypoint in (convert_file, convert):
                with self.subTest(width=width, entrypoint=entrypoint.__name__):
                    with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                        entrypoint(self.source, layout="vertical-optimized",
                                   layout_width=width, out_dir=out_dir)
                    self.assertFalse(out_dir.exists())
                    self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")

    def test_width_option_requires_vertical_layout(self):
        self.output.write_text("previous output", encoding="utf-8")
        for name in ("default", "optimized"):
            with self.subTest(layout=name):
                with self.assertRaisesRegex(ValueError, "vertical"):
                    render_svg(self.graph, layout=name, layout_width=1200)
                status, _, errors = self.invoke(str(self.source), "--layout", name,
                                                 "--layout-width", "1200")
                self.assertEqual(status, 1)
                self.assertIn("vertical", errors)
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")

    def test_vertical_svg_has_responsive_intrinsic_dimensions_in_both_modes(self):
        class CustomVerticalLayout(VerticalOptimizedLayout):
            pass

        for selected in ("vertical-optimized", VerticalOptimizedLayout, CustomVerticalLayout):
            for interactive in (False, True):
                with self.subTest(layout=selected, interactive=interactive):
                    root = ET.fromstring(render_svg(self.graph, layout=selected,
                                                    interactive=interactive))
                    _, _, view_width, view_height = map(float, root.get("viewBox").split())
                    self.assertEqual(float(root.get("width")), view_width)
                    self.assertEqual(float(root.get("height")), view_height)
                    styles = dict(item.split(":", 1) for item in root.get("style").split(";") if item)
                    self.assertEqual(styles["max-width"], "100%")
                    self.assertEqual(styles["height"], "auto")
                    self.assertEqual(styles["display"], "block")
                    self.assertEqual(root.find(SVG + "script") is not None, interactive)

    def test_existing_layout_svg_sizing_is_unchanged(self):
        for name in ("default", "optimized"):
            for interactive in (False, True):
                with self.subTest(layout=name, interactive=interactive):
                    root = ET.fromstring(render_svg(self.graph, layout=name, interactive=interactive))
                    self.assertIsNone(root.get("style"))
                    if interactive:
                        self.assertEqual(root.get("width"), "100%")
                        self.assertEqual(root.get("height"), "100%")
                    else:
                        self.assertGreater(float(root.get("width")), 0)
                        self.assertGreater(float(root.get("height")), 0)

    def test_vertical_options_and_output_directory_work_together(self):
        out_dir = self.tmp_path / "svg-output"
        options = dict(layout="vertical-optimized", layout_width=1440,
                       router="orthogonal", interactive=True, color_type_edges=True,
                       show_imports=True, show_nested_types=True,
                       hide=["packet::second"], treeshake=True)
        expected = render_svg(self.graph, **options)
        status, stdout, errors = self.invoke(
            str(self.source), "--layout", "vertical-optimized", "--layout-width", "1440",
            "--router", "orthogonal", "--interactive", "--color-type-edges",
            "--show-imports", "--show-nested-types", "--hide", "packet::second",
            "--treeshake", "--out-dir", str(out_dir),
        )
        self.assertEqual(status, 0, errors)
        output = out_dir / "packet.svg"
        self.assertIn(str(output), stdout)
        self.assertFalse(self.output.exists())
        self.assertEqual(output.read_text(encoding="utf-8"), expected)
        for entrypoint in (convert_file, convert):
            self.assertEqual(entrypoint(self.source, out_dir=out_dir, **options), output)
            self.assertEqual(output.read_text(encoding="utf-8"), expected)
        root = ET.fromstring(expected)
        self.assertFalse(any(element.get("data-row-id") == "second"
                             for element in root.iter(SVG + "g")))
        self.assertFalse(any(element.get("data-node-id") == "common::unused"
                             for element in root.iter(SVG + "g")))


if __name__ == "__main__":
    unittest.main()
