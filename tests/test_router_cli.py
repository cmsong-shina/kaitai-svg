from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from itertools import product
from unittest import mock
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import DefaultRouter, OrthogonalRouter, Router, convert
from kaitai_svg.cli import convert_file, main
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.render import render_svg
from kaitai_svg.routing import ROUTERS


SVG = "{http://www.w3.org/2000/svg}"


class RouterCliTests(SchemaTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.schema("packet.ksy", """
            meta: {id: packet, imports: [common]}
            seq:
              - {id: first, type: body, doc: First body}
              - {id: second, type: body}
              - {id: shared, type: common}
            types:
              body:
                seq:
                  - {id: value, type: u1}
        """)
        self.schema("common.ksy", "meta: {id: common}\n")
        self.output = self.source.with_suffix(".svg")

    def invoke(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_router_classes_are_public_and_registry_matches_choices(self):
        self.assertIs(ROUTERS["default"], DefaultRouter)
        self.assertIs(ROUTERS["orthogonal"], OrthogonalRouter)
        self.assertTrue(issubclass(DefaultRouter, Router))
        self.assertTrue(issubclass(OrthogonalRouter, Router))

    def test_omitted_router_matches_explicit_default_name_and_class(self):
        graph = load_graph(self.source)
        expected = render_svg(graph)
        self.assertEqual(expected, render_svg(graph, router="default"))
        self.assertEqual(expected, render_svg(graph, router=DefaultRouter))
        for entrypoint in (convert_file, convert):
            for options in ({}, {"router": "default"}, {"router": DefaultRouter}):
                with self.subTest(entrypoint=entrypoint.__name__, options=options):
                    self.assertEqual(entrypoint(self.source, **options), self.output)
                    self.assertEqual(self.output.read_text(encoding="utf-8"), expected)
        status, _, errors = self.invoke(str(self.source))
        self.assertEqual(status, 0, errors)
        self.assertEqual(self.output.read_text(encoding="utf-8"), expected)

    def test_each_cli_router_generates_requested_routes(self):
        graph = load_graph(self.source)
        for name in ROUTERS:
            with self.subTest(router=name):
                status, stdout, errors = self.invoke(str(self.source), "--router", name)
                self.assertEqual(status, 0, errors)
                self.assertIn(str(self.output), stdout)
                self.assertEqual(ET.parse(self.output).getroot().tag, SVG + "svg")
                self.assertEqual(self.output.read_text(encoding="utf-8"),
                                 render_svg(graph, router=name))

    def test_invalid_cli_router_is_rejected_before_conversion(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        stderr = StringIO()
        with redirect_stderr(stderr), mock.patch("kaitai_svg.cli.convert_file") as converter:
            with self.assertRaises(SystemExit) as raised:
                main([str(self.source), "--router", "unknown"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        converter.assert_not_called()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_unknown_api_router_preserves_existing_output(self):
        self.output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        with self.assertRaisesRegex(ValueError, "[Rr]outer"):
            render_svg(load_graph(self.source), router="unknown")
        for entrypoint in (convert_file, convert):
            with self.subTest(entrypoint=entrypoint.__name__):
                with self.assertRaisesRegex(ValueError, "[Rr]outer"):
                    entrypoint(self.source, router="unknown")
                self.assertEqual(self.output.read_text(encoding="utf-8"), "previous output")
                self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_api_router_selection_reaches_layout(self):
        graph = load_graph(self.source)
        for selected in ("orthogonal", OrthogonalRouter):
            with self.subTest(router=selected):
                with mock.patch("kaitai_svg.render.layout_graph", wraps=layout_graph) as layout:
                    convert(self.source, router=selected)
                self.assertEqual(layout.call_count, 1)
                self.assertIs(layout.call_args.kwargs["router"], selected)
                self.assertEqual(self.output.read_text(encoding="utf-8"),
                                 render_svg(graph, router=selected))

    def test_orthogonal_router_composes_with_all_display_options(self):
        for use_api in (False, True):
            for imports, nested, interactive, colors in product((False, True), repeat=4):
                options = dict(show_imports=imports, show_nested_types=nested,
                               interactive=interactive, color_type_edges=colors)
                with self.subTest(use_api=use_api, **options):
                    if use_api:
                        convert(self.source, router="orthogonal", **options)
                    else:
                        arguments = [str(self.source), "--router", "orthogonal"]
                        arguments.extend("--" + name.replace("_", "-")
                                         for name, enabled in options.items() if enabled)
                        status, _, errors = self.invoke(*arguments)
                        self.assertEqual(status, 0, errors)
                    xml = ET.parse(self.output).getroot()
                    groups = list(xml.iter(SVG + "g"))
                    edges = [g for g in groups if "edge" in g.get("class", "").split()]
                    self.assertEqual(len(edges), 3 + imports + nested)
                    self.assertEqual(sum("edge-kind-import" in g.get("class", "").split()
                                         for g in edges), int(imports))
                    self.assertEqual(sum("edge-kind-contains" in g.get("class", "").split()
                                         for g in edges), int(nested))
                    self.assertEqual(xml.find(SVG + "script") is not None, interactive)
                    self.assertEqual(any(g.get("id") == "controls" for g in groups), interactive)
                    type_edges = [g for g in edges if "edge-kind-type" in g.get("class", "").split()]
                    markers = {g.find(f'{SVG}path[@class="edge-path"]').get("marker-end")
                               for g in type_edges}
                    self.assertEqual(len(markers), 3 if colors else 1)
                    self.assertTrue(any("First body" in (title.text or "")
                                        for title in xml.iter(SVG + "title")))


if __name__ == "__main__":
    unittest.main()
