from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import DefaultLayout, LayoutEngine, OptimizedLayout, convert
from kaitai_svg.cli import convert_file, main
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.placement import LAYOUTS
from kaitai_svg.render import render_svg
from kaitai_svg.routing import ROUTERS


SVG = "{http://www.w3.org/2000/svg}"


class LayoutCliTests(SchemaTestCase):
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

    def test_layout_classes_are_public_and_registry_matches_choices(self):
        self.assertIs(LAYOUTS["default"], DefaultLayout)
        self.assertIs(LAYOUTS["optimized"], OptimizedLayout)
        self.assertTrue(issubclass(DefaultLayout, LayoutEngine))
        self.assertTrue(issubclass(OptimizedLayout, LayoutEngine))

    def test_omitted_layout_matches_explicit_default_name_and_class(self):
        expected = render_svg(self.graph)
        self.assertEqual(expected, render_svg(self.graph, layout="default"))
        self.assertEqual(expected, render_svg(self.graph, layout=DefaultLayout))
        for entrypoint in (convert_file, convert):
            for options in ({}, {"layout": "default"}, {"layout": DefaultLayout}):
                with self.subTest(entrypoint=entrypoint.__name__, options=options):
                    self.assertEqual(entrypoint(self.source, **options), self.output)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), expected)
        status, _, errors = self.invoke(str(self.source))
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.output.read_text(encoding="utf-8"), expected)

    def test_cli_layouts_compose_with_each_router(self):
        for name in LAYOUTS:
            for router in ROUTERS:
                with self.subTest(layout=name, router=router):
                    status, stdout, errors = self.invoke(str(self.source), "--layout", name,
                                                         "--router", router)
                    self.assertEqual(status, 0, errors)
                    self.assertIn(str(self.output), stdout)
                    self.assertEqual(ET.parse(self.output).getroot().tag, SVG + "svg")
                    self.assertEqual(self.output.read_text(encoding="utf-8"),
                                     render_svg(self.graph, layout=name, router=router))

    def test_api_layout_selection_reaches_layout_graph(self):
        for selected in ("optimized", OptimizedLayout):
            with self.subTest(layout=selected):
                with mock.patch("kaitai_svg.render.layout_graph", wraps=layout_graph) as builder:
                    convert(self.source, layout=selected, router="orthogonal")
                self.assertEqual(builder.call_count, 1)
                self.assertIs(builder.call_args.kwargs["layout"], selected)
                self.assertEqual(self.output.read_text(encoding="utf-8"),
                                 render_svg(self.graph, layout=selected, router="orthogonal"))

    def test_invalid_cli_layout_is_rejected_before_conversion(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        stderr = StringIO()
        with redirect_stderr(stderr), mock.patch("kaitai_svg.cli.convert_file") as converter:
            with self.assertRaises(SystemExit) as raised:
                main([str(self.source), "--layout", "unknown"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        converter.assert_not_called()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_unknown_api_layout_preserves_existing_output(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        with self.assertRaisesRegex(ValueError, "[Ll]ayout"):
            render_svg(self.graph, layout="unknown")
        for entrypoint in (convert_file, convert):
            with self.subTest(entrypoint=entrypoint.__name__):
                with self.assertRaisesRegex(ValueError, "[Ll]ayout"):
                    entrypoint(self.source, layout="unknown")
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
                self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_optimized_layout_preserves_display_and_visibility_options(self):
        combinations = (
            dict(show_imports=True, show_nested_types=True, interactive=True,
                 color_type_edges=True, hide=["packet::second"], treeshake=True),
            dict(show_imports=False, show_nested_types=True, interactive=False,
                 color_type_edges=True, hide=["common.ksy"], treeshake=False),
            dict(show_imports=True, show_nested_types=False, interactive=True,
                 color_type_edges=False, hide=[], treeshake=True),
            dict(show_imports=False, show_nested_types=False, interactive=False,
                 color_type_edges=False, hide=["packet.ksy", "common.ksy"], treeshake=True),
        )
        for index, options in enumerate(combinations):
            router = "orthogonal" if index % 2 == 0 else "default"
            expected = render_svg(self.graph, layout="optimized", router=router, **options)
            arguments = [str(self.source), "--layout", "optimized", "--router", router]
            for name, value in options.items():
                if name == "hide":
                    for selector in value:
                        arguments.extend(["--hide", selector])
                elif value:
                    arguments.append("--" + name.replace("_", "-"))
            with self.subTest(options=options, router=router):
                status, _, errors = self.invoke(*arguments)
                self.assertEqual(status, 0, errors)
                self.assertEqual(self.output.read_text(encoding="utf-8"), expected)
                for entrypoint in (convert_file, convert):
                    entrypoint(self.source, layout=OptimizedLayout, router=router, **options)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), expected)
                xml = ET.fromstring(expected)
                self.assertEqual(xml.find(SVG + "script") is not None, options["interactive"])
                titles = {group.get("aria-label") for group in xml.iter(SVG + "g")
                          if group.get("data-node-id") is not None}
                if options["treeshake"]:
                    self.assertNotIn("common::unused", titles)
                if "common.ksy" in options["hide"]:
                    self.assertFalse(any(title.startswith("common") for title in titles))
                if "packet.ksy" in options["hide"]:
                    self.assertEqual(titles, set())
                rows = {text.text for text in xml.iter(SVG + "text")
                        if text.get("class") == "field-label"}
                if "packet::second" in options["hide"]:
                    self.assertNotIn("second", rows)


if __name__ == "__main__":
    unittest.main()
