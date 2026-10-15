from __future__ import annotations

from contextlib import chdir, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
from itertools import product
import os
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import convert
from kaitai_svg.cli import convert_file, main
from kaitai_svg.loader import SchemaError, load_graph
from kaitai_svg.model import Graph
from kaitai_svg.render import render_svg


SVG = "{http://www.w3.org/2000/svg}"


def node_titles(xml):
    return {group.get("aria-label") for group in xml.iter(SVG + "g")
            if group.get("data-node-id") is not None}


class HideTests(SchemaTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.schema("packet.ksy", """
            meta: {id: packet, imports: [shared/common]}
            params:
              - {id: count, type: u1}
            seq:
              - {id: body, type: body}
              - {id: shared, type: common::payload}
              - {id: status, type: u1, enum: state}
              - {id: tail, type: u1}
            instances:
              computed: {value: tail + 1}
            types:
              body:
                seq:
                  - {id: value, type: inner}
                types:
                  inner:
                    seq:
                      - {id: value, type: u1}
            enums:
              state:
                0: idle
                1: busy
        """)
        self.imported = self.schema("shared/common.ksy", """
            meta: {id: common}
            types:
              payload:
                seq:
                  - {id: value, type: u2}
        """)
        self.graph = load_graph(self.source)

    def test_local_qualified_and_generated_node_ids_hide_only_matching_definitions(self):
        body = next(node for node in self.graph.nodes if node.title == "packet::body")
        for selector in ("body", "packet::body", body.id):
            with self.subTest(selector=selector):
                xml = ET.fromstring(render_svg(self.graph, hide=[selector],
                                              show_imports=True, show_nested_types=True))
                self.assertNotIn("packet::body", node_titles(xml))
                self.assertIn("packet::body::inner", node_titles(xml))
                self.assertIn("common::payload", node_titles(xml))
                self.assertFalse(any(path.get("data-source") == body.id or path.get("data-target") == body.id
                                     for path in xml.iter(SVG + "path")))

    def test_qualified_ids_disambiguate_rows_and_short_ids_match_all_scopes(self):
        for selectors, remaining in ((["value"], 0), (["body::value"], 2),
                                     (["packet::body::inner::value"], 2)):
            with self.subTest(selectors=selectors):
                xml = ET.fromstring(render_svg(self.graph, hide=selectors))
                labels = [text.text for text in xml.iter(SVG + "text") if text.get("class") == "field-label"]
                self.assertEqual(labels.count("value"), remaining)

    def test_fields_params_instances_and_enum_values_can_be_hidden(self):
        selectors = ["packet::shared", "count", "computed", "state::idle"]
        xml = ET.fromstring(render_svg(self.graph, hide=selectors))
        labels = {text.text for text in xml.iter(SVG + "text") if text.get("class") == "field-label"}
        self.assertTrue({"shared", "count", "computed", "idle"}.isdisjoint(labels))
        self.assertIn("busy", labels)
        self.assertIn("common::payload", node_titles(xml))
        self.assertFalse(any(path.get("data-source-row") == "/seq/1" for path in xml.iter(SVG + "path")))

    def test_enum_and_raw_row_ids_are_supported(self):
        xml = ET.fromstring(render_svg(self.graph, hide=["state", "/seq/1"]))
        self.assertNotIn("packet::state", node_titles(xml))
        self.assertFalse(any(group.get("data-row-id") == "/seq/1" for group in xml.iter(SVG + "g")))

    def test_filenames_hide_every_definition_and_remove_empty_groups(self):
        for selector in ("common.ksy", "shared/common.ksy", "shared\\common.ksy",
                         Path("shared/./common.ksy")):
            with self.subTest(selector=selector):
                xml = ET.fromstring(render_svg(self.graph, hide=[selector], show_imports=True))
                self.assertTrue(all(not title.startswith("common") for title in node_titles(xml)))
                self.assertFalse(any(group.get("data-source-file") == "shared/common.ksy"
                                     for group in xml.iter(SVG + "g")))
                root = next(group for group in xml.iter(SVG + "g") if group.get("aria-label") == "packet"
                            and group.get("data-node-id") is not None)
                self.assertTrue(any(group.get("data-row-id") == "/seq/1" for group in root.iter(SVG + "g")))

    def test_source_paths_distinguish_same_basename_imports(self):
        other = self.schema("common.ksy", "meta: {id: other}\n")
        extra = load_graph(other).nodes[0]
        extra = replace(extra, id="other", source_file="common.ksy")
        graph = Graph(self.graph.title, [*self.graph.nodes, extra], self.graph.edges)
        for selector, expected in (("common.ksy", set()), ("./common.ksy", {"common", "common::payload"}),
                                   ("shared/common.ksy", {"other"})):
            with self.subTest(selector=selector):
                xml = ET.fromstring(render_svg(graph, hide=[selector]))
                self.assertEqual(node_titles(xml) & {"other", "common", "common::payload"}, expected)

    def test_filtering_precedes_layout_and_preserves_graph_and_sizes(self):
        before = deepcopy(self.graph)
        root = next(node for node in self.graph.nodes if node.title == "packet")
        hidden_node_ids = {node.id for node in self.graph.nodes if node.source_file == "shared/common.ksy"}
        nodes = [replace(node, rows=[row for row in node.rows if row.label != "status"])
                 if node.id == root.id else node
                 for node in self.graph.nodes if node.id not in hidden_node_ids]
        edges = [edge for edge in self.graph.edges
                 if edge.source not in hidden_node_ids and edge.target not in hidden_node_ids
                 and (edge.source, edge.source_row) != (root.id, "/seq/2")]
        expected = Graph(self.graph.title, nodes, edges)
        for interactive, router in product((False, True), ("default", "orthogonal")):
            with self.subTest(interactive=interactive, router=router):
                options = dict(interactive=interactive, router=router, color_type_edges=True,
                               show_imports=True, show_nested_types=True)
                self.assertEqual(render_svg(self.graph, hide=["common.ksy", "packet::status"], **options),
                                 render_svg(expected, **options))
        self.assertEqual(self.graph, before)

    def test_unknown_duplicate_and_empty_selectors(self):
        self.assertEqual(render_svg(self.graph, hide=[]), render_svg(self.graph))
        self.assertEqual(render_svg(self.graph, hide=["missing", "missing.ksy"]), render_svg(self.graph))
        self.assertEqual(render_svg(self.graph, hide=["state", "state"]), render_svg(self.graph, hide=["state"]))

    def test_all_definitions_can_be_hidden(self):
        xml = ET.fromstring(render_svg(self.graph, hide=["packet.ksy", "common.ksy"], interactive=True))
        self.assertEqual(node_titles(xml), set())
        self.assertFalse(any(group.get("class") == "file-group" for group in xml.iter(SVG + "g")))
        self.assertIn("0 definitions", "".join(xml.itertext()))
        self.assertIn("This schema has no definitions.", "".join(xml.itertext()))

    def test_repeated_cli_flags_and_python_entrypoints(self):
        selectors = ["common.ksy", "packet::body", "tail"]
        expected = render_svg(self.graph, hide=selectors, interactive=True, show_imports=True,
                              show_nested_types=True, color_type_edges=True, router="orthogonal")
        arguments = [str(self.source), "--interactive", "--show-imports", "--show-nested-types",
                     "--color-type-edges", "--router", "orthogonal"]
        for selector in selectors:
            arguments.extend(["--hide", selector])
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            self.assertEqual(main(arguments), 0, errors.getvalue())
        self.assertEqual(self.source.with_suffix(".svg").read_text(encoding="utf-8"), expected)
        for entrypoint in (convert_file, convert):
            with self.subTest(entrypoint=entrypoint.__name__):
                output = entrypoint(self.source, hide=selectors, interactive=True, show_imports=True,
                                    show_nested_types=True, color_type_edges=True, router="orthogonal")
                self.assertEqual(output.read_text(encoding="utf-8"), expected)
                output = entrypoint(self.source)
                self.assertEqual(output.read_text(encoding="utf-8"), render_svg(self.graph))

    def test_conversion_accepts_absolute_and_working_directory_relative_paths(self):
        expected = render_svg(self.graph, hide=["shared/common.ksy"])
        relative = os.path.relpath(self.imported)
        for selector in (self.imported, str(self.imported), relative):
            with self.subTest(selector=selector):
                output = convert(self.source, hide=[selector])
                self.assertEqual(output.read_text(encoding="utf-8"), expected)

    def test_bare_filenames_keep_basename_matching_when_file_exists_in_cwd(self):
        unrelated = self.schema("elsewhere/common.ksy", "meta: {id: unrelated}\n")
        with chdir(unrelated.parent):
            output = convert(self.source, hide=["common.ksy"])
        self.assertEqual(output.read_text(encoding="utf-8"), render_svg(self.graph, hide=["common.ksy"]))

    def test_hidden_import_is_still_validated_and_output_is_preserved(self):
        self.imported.write_text("meta: {id: common}\nseq: invalid\n", encoding="utf-8")
        output = self.source.with_suffix(".svg")
        output.write_text("previous output", encoding="utf-8")
        with self.assertRaises(SchemaError):
            convert(self.source, hide=["common.ksy"])
        self.assertEqual(output.read_text(encoding="utf-8"), "previous output")


if __name__ == "__main__":
    unittest.main()
