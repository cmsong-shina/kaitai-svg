from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import LayoutEngine, OptimizedWithSpaceLayout, convert
from kaitai_svg.cli import convert_file, main
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.placement import LAYOUTS
from kaitai_svg.render import render_svg
from kaitai_svg.routing import ROUTERS


SVG = "{http://www.w3.org/2000/svg}"


class SpaceLayoutCliTests(SchemaTestCase):
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

    def test_space_layout_is_public_and_registered_without_vertical_interaction(self):
        self.assertIs(LAYOUTS["optimized-with-space"], OptimizedWithSpaceLayout)
        self.assertTrue(issubclass(OptimizedWithSpaceLayout, LayoutEngine))
        self.assertTrue(OptimizedWithSpaceLayout.supports_width_limit)
        self.assertFalse(OptimizedWithSpaceLayout.web_vertical)

    def test_cli_auto_and_capped_width_compose_with_each_router(self):
        for router in ROUTERS:
            for width in (None, 1440):
                with self.subTest(router=router, width=width):
                    arguments = [str(self.source), "--layout", "optimized-with-space",
                                 "--router", router]
                    if width is not None:
                        arguments.extend(["--layout-width", str(width)])
                    status, stdout, errors = self.invoke(*arguments)
                    self.assertEqual(status, 0, errors)
                    self.assertIn(str(self.output), stdout)
                    expected = render_svg(self.graph, layout="optimized-with-space",
                                          router=router, layout_width=width)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), expected)
                    root = ET.fromstring(expected)
                    self.assertIsNone(root.get("style"))
                    _, _, view_width, view_height = map(float, root.get("viewBox").split())
                    self.assertEqual(float(root.get("width")), view_width)
                    self.assertEqual(float(root.get("height")), view_height)
                    if width is not None:
                        self.assertLessEqual(view_width, width)

    def test_conversion_apis_forward_name_class_and_fractional_width(self):
        for entrypoint in (convert_file, convert):
            for selected in ("optimized-with-space", OptimizedWithSpaceLayout):
                with self.subTest(entrypoint=entrypoint.__name__, layout=selected):
                    with mock.patch("kaitai_svg.render.layout_graph", wraps=layout_graph) as builder:
                        self.assertEqual(entrypoint(self.source, layout=selected,
                                                    layout_width=1440.5), self.output)
                    self.assertEqual(builder.call_count, 1)
                    self.assertIs(builder.call_args.kwargs["layout"], selected)
                    self.assertEqual(builder.call_args.kwargs["layout_width"], 1440.5)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), render_svg(
                        self.graph, layout=selected, layout_width=1440.5,
                    ))

    def test_space_layout_keeps_regular_static_and_interactive_svg_sizing(self):
        class CustomSpaceLayout(OptimizedWithSpaceLayout):
            pass

        for selected in ("optimized-with-space", OptimizedWithSpaceLayout, CustomSpaceLayout):
            for interactive in (False, True):
                with self.subTest(layout=selected, interactive=interactive):
                    root = ET.fromstring(render_svg(self.graph, layout=selected,
                                                    interactive=interactive))
                    self.assertIsNone(root.get("style"))
                    self.assertEqual(root.find(SVG + "script") is not None, interactive)
                    if interactive:
                        self.assertEqual(root.get("width"), "100%")
                        self.assertEqual(root.get("height"), "100%")
                    else:
                        self.assertGreater(float(root.get("width")), 0)
                        self.assertGreater(float(root.get("height")), 0)

    def test_visibility_options_and_output_directory_work_through_cli_and_apis(self):
        cases = (
            dict(show_imports=True, show_nested_types=True, interactive=True,
                 color_type_edges=True, hide=["packet::second"], treeshake=True),
            dict(show_imports=False, show_nested_types=True, interactive=False,
                 color_type_edges=True, hide=["common.ksy"], treeshake=False),
            dict(show_imports=True, show_nested_types=False, interactive=True,
                 color_type_edges=False, hide=[], treeshake=True),
            dict(show_imports=False, show_nested_types=False, interactive=False,
                 color_type_edges=False, hide=["packet.ksy", "common.ksy"], treeshake=True),
        )
        out_dir = self.tmp_path / "svg-output"
        output = out_dir / "packet.svg"
        for index, options in enumerate(cases):
            router = "orthogonal" if index % 2 == 0 else "default"
            entrypoint = (convert_file, convert)[index % 2]
            with self.subTest(options=options, router=router):
                expected = render_svg(self.graph, layout="optimized-with-space",
                                      layout_width=1440, router=router, **options)
                arguments = [str(self.source), "--layout", "optimized-with-space",
                             "--layout-width", "1440", "--router", router,
                             "--out-dir", str(out_dir)]
                for name, value in options.items():
                    if name == "hide":
                        for selector in value:
                            arguments.extend(["--hide", selector])
                    elif value:
                        arguments.append("--" + name.replace("_", "-"))
                status, stdout, errors = self.invoke(*arguments)
                self.assertEqual(status, 0, errors)
                self.assertIn(str(output), stdout)
                self.assertFalse(self.output.exists())
                self.assertEqual(output.read_text(encoding="utf-8"), expected)
                self.assertEqual(entrypoint(self.source, layout=OptimizedWithSpaceLayout,
                                            layout_width=1440, router=router,
                                            out_dir=out_dir, **options), output)
                self.assertEqual(output.read_text(encoding="utf-8"), expected)
                root = ET.fromstring(expected)
                self.assertEqual(root.find(SVG + "script") is not None, options["interactive"])
                titles = {group.get("aria-label") for group in root.iter(SVG + "g")
                          if group.get("data-node-id") is not None}
                if options["treeshake"]:
                    self.assertNotIn("common::unused", titles)
                if "common.ksy" in options["hide"]:
                    self.assertFalse(any(title.startswith("common") for title in titles))
                if "packet.ksy" in options["hide"]:
                    self.assertEqual(titles, set())
                if "packet::second" in options["hide"]:
                    self.assertFalse(any(group.get("data-row-id") == "second"
                                         for group in root.iter(SVG + "g")))

    def test_invalid_cli_width_is_rejected_before_conversion_or_writes(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        for width in ("0", "-1", "1.5", "nan", "infinity", "wide"):
            with self.subTest(width=width):
                stderr = StringIO()
                with redirect_stderr(stderr), mock.patch("kaitai_svg.cli.convert_file") as converter:
                    with self.assertRaises(SystemExit) as raised:
                        main([str(self.source), "--layout", "optimized-with-space",
                              "--layout-width", width])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("positive integer", stderr.getvalue())
                converter.assert_not_called()
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
                self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_invalid_or_impossible_api_cap_preserves_output_and_uncreated_directory(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        out_dir = self.tmp_path / "new-output"
        for width in (0, -1, float("inf"), float("nan"), True, 679):
            for entrypoint in (convert_file, convert):
                with self.subTest(width=width, entrypoint=entrypoint.__name__):
                    with self.assertRaisesRegex(ValueError, "[Ww]idth"):
                        entrypoint(self.source, layout="optimized-with-space",
                                   layout_width=width, out_dir=out_dir)
                    self.assertFalse(out_dir.exists())
                    self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
                    self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_unroutable_cli_cap_preserves_output_without_temporary_files(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        status, stdout, errors = self.invoke(
            str(self.source), "--layout", "optimized-with-space", "--layout-width", "679",
        )
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("width", errors.lower())
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(set(self.tmp_path.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
