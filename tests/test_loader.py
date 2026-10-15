from __future__ import annotations

from pathlib import Path
import os
import unittest
from unittest import mock

from helpers import SchemaTestCase

from kaitai_svg.loader import SchemaError, load_graph


def node_named(graph, name: str):
    matches = [node for node in graph.nodes if node.title.split("::")[-1] == name]
    assert len(matches) == 1, [(node.id, node.title) for node in graph.nodes]
    return matches[0]


class LoaderTests(SchemaTestCase):
    def test_official_schema_rejects_invalid_structure(self):
        for source in [
            "meta: {id: broken}\nseq: wrong\n",
            "meta: {id: 123}\n",
            "meta: {id: broken, imports: wrong}\n",
            "meta: {id: broken}\nseq:\n  - id: field\n    type: 42\n",
        ]:
            with self.subTest(source=source):
                path = self.schema("broken.ksy", source)
                with self.assertRaises(SchemaError) as caught:
                    load_graph(path)
                self.assertIn("broken.ksy", str(caught.exception))


    def test_imported_schema_is_validated(self):
        root = self.schema("root.ksy", "meta: {id: root, imports: [broken]}\n")
        self.schema("broken.ksy", "meta: {id: broken}\nseq: not_a_sequence\n")
        with self.assertRaises(SchemaError) as caught:
            load_graph(root)
        assert "broken.ksy" in str(caught.exception)


    def test_missing_import_reports_its_name(self):
        root = self.schema("root.ksy", "meta: {id: root, imports: [absent]}\n")
        with self.assertRaisesRegex(SchemaError, "absent"):
            load_graph(root)


    def test_yaml_syntax_error_has_schema_path(self):
        path = self.schema("broken.ksy", "meta: [unterminated\n")
        with self.assertRaises(SchemaError) as caught:
            load_graph(path)
        assert "broken.ksy" in str(caught.exception)

    def test_duplicate_yaml_keys_are_rejected(self):
        root = self.schema("duplicate.ksy", "meta:\n  id: first\n  id: second\n")
        with self.assertRaisesRegex(SchemaError, "duplicate"):
            load_graph(root)

    def test_utf8_bom_and_yaml_identifiers_are_preserved(self):
        root = self.schema(
            "bom.ksy",
            "\ufeffmeta: {id: bom}\nseq:\n  - id: on\n    type: u1\n    enum: state\nenums:\n  state:\n    0: off\n    1: on\n",
        )
        graph = load_graph(root)
        node = node_named(graph, "bom")
        self.assertEqual(node.rows[0].label, "on")
        enum = node_named(graph, "state")
        self.assertEqual({row.label for row in enum.rows}, {"off", "on"})


    def test_relative_import_resolves_from_importer(self):
        root = self.schema(
            "formats/packet.ksy",
            """
            meta:
              id: packet
              imports: [shared/common]
            seq:
              - id: header
                type: common::header
            """,
        )
        self.schema(
            "formats/shared/common.ksy",
            """
            meta: {id: common, endian: le}
            types:
              header:
                seq:
                  - id: size
                    type: u2
            """,
        )
        graph = load_graph(root)
        header = node_named(graph, "header")
        packet = node_named(graph, "packet")
        assert any(edge.source == packet.id and edge.target == header.id for edge in graph.edges)


    def test_library_import_search_path(self):
        root = self.schema(
            "formats/packet.ksy",
            """
            meta: {id: packet, imports: [/library]}
            seq:
              - id: header
                type: library::header
            """,
        )
        self.schema(
            "imports/library.ksy",
            """
            meta: {id: library}
            types:
              header:
                seq:
                  - id: value
                    type: u1
            """,
        )
        graph = load_graph(root, import_paths=[self.tmp_path / "imports"])
        header = node_named(graph, "header")
        assert any(edge.target == header.id for edge in graph.edges)

    def test_kspath_uses_platform_separator_and_explicit_paths_take_precedence(self):
        root = self.schema("packet.ksy", "meta: {id: packet, imports: [/library]}\n")
        self.schema("first/library.ksy", "meta: {id: library}\ndoc: from first path\n")
        self.schema("second/library.ksy", "meta: {id: library}\ndoc: from second path\n")
        paths = os.pathsep.join(str(self.tmp_path / name) for name in ("missing", "first", "second"))
        with mock.patch.dict(os.environ, {"KSPATH": paths}):
            from_environment = load_graph(root)
            explicit_override = load_graph(root, import_paths=[self.tmp_path / "second"])
        self.assertEqual(node_named(from_environment, "library").doc, "from first path")
        self.assertEqual(node_named(explicit_override, "library").doc, "from second path")

    def test_transitive_imports_resolve_from_each_importing_directory(self):
        root = self.schema("packet.ksy", "meta: {id: packet, imports: [nested/common]}\n")
        self.schema(
            "nested/common.ksy",
            "meta: {id: common, imports: [deeper/leaf]}\nseq:\n  - id: child\n    type: leaf\n",
        )
        leaf = self.schema("nested/deeper/leaf.ksy", "meta: {id: leaf}\nseq:\n  - id: value\n    type: u1\n")
        graph = load_graph(root)
        common, target = node_named(graph, "common"), node_named(graph, "leaf")
        self.assertTrue(any(edge.source == common.id and edge.target == target.id and edge.kind == "type" for edge in graph.edges))
        leaf.write_text("meta: {id: leaf}\nseq: invalid\n", encoding="utf-8")
        with self.assertRaisesRegex(SchemaError, "leaf.ksy"):
            load_graph(root)

    def test_multiple_imports_of_same_schema_share_one_definition(self):
        root = self.schema("packet.ksy", "meta: {id: packet, imports: [first, second]}\n")
        self.schema("first.ksy", "meta: {id: first, imports: [shared]}\n")
        self.schema("second.ksy", "meta: {id: second, imports: [shared]}\n")
        self.schema("shared.ksy", "meta: {id: shared}\n")
        graph = load_graph(root)
        shared = node_named(graph, "shared")
        self.assertEqual(len(graph.nodes), 4)
        self.assertEqual(sum(edge.target == shared.id for edge in graph.edges), 2)

    def test_yaml_merge_can_override_defaults(self):
        root = self.schema(
            "merged.ksy",
            """
            meta: {id: merged}
            seq:
              - &defaults
                id: first
                type: u1
                doc: shared documentation
              - <<: *defaults
                id: second
            """,
        )
        rows = node_named(load_graph(root), "merged").rows
        self.assertEqual([row.label for row in rows], ["first", "second"])
        self.assertEqual([row.doc for row in rows], ["shared documentation"] * 2)

    def test_recursive_yaml_alias_is_rejected(self):
        root = self.schema("recursive.ksy", "meta: {id: recursive}\ndoc: &recursive [*recursive]\n")
        with self.assertRaisesRegex(SchemaError, "recursive"):
            load_graph(root)

    def test_parameter_array_types_and_opaque_types(self):
        root = self.schema(
            "parameters.ksy",
            """
            meta: {id: parameters, ks-opaque-types: true}
            params:
              - id: records
                type: entry[]
            seq:
              - id: external
                type: external_type
            types:
              entry:
                seq:
                  - id: value
                    type: u1
            """,
        )
        graph = load_graph(root)
        parent, entry = node_named(graph, "parameters"), node_named(graph, "entry")
        parameter = next(row for row in parent.rows if row.label == "records")
        self.assertEqual(parameter.kind, "param")
        self.assertTrue(any(edge.source_row == parameter.id and edge.target == entry.id for edge in graph.edges))
        opaque = node_named(graph, "external_type")
        self.assertEqual(opaque.kind, "opaque")

    def test_bit_widths_with_leading_zeroes_are_primitive_types(self):
        root = self.schema(
            "bits.ksy",
            "meta: {id: bits}\nparams: [{id: flags, type: 'b08[]'}]\nseq: [{id: enabled, type: b01}]\n",
        )
        graph = load_graph(root)
        self.assertEqual([row.type_label for row in graph.nodes[0].rows], ["b08[]", "b01"])
        self.assertFalse(graph.edges)

    def test_parameter_types_use_pure_type_names(self):
        root = self.schema(
            "pure.ksy",
            """
            meta: {id: pure}
            params:
              - id: text_record
                type: strz
              - id: endian_record
                type: u2le[]
            types:
              strz: {}
              u2le: {}
            """,
        )
        graph = load_graph(root)
        parent, text, endian = (node_named(graph, name) for name in ("pure", "strz", "u2le"))
        self.assertTrue(any(edge.source == parent.id and edge.target == text.id and edge.source_row == "/params/0" for edge in graph.edges))
        self.assertTrue(any(edge.source == parent.id and edge.target == endian.id and edge.source_row == "/params/1" for edge in graph.edges))


    def test_cyclic_imports_terminate_and_resolve_types(self):
        root = self.schema(
            "alpha.ksy",
            """
            meta: {id: alpha, imports: [beta]}
            seq:
              - id: other
                type: beta::body
            types:
              footer:
                seq:
                  - id: checksum
                    type: u1
            """,
        )
        self.schema(
            "beta.ksy",
            """
            meta: {id: beta, imports: [alpha]}
            types:
              body:
                seq:
                  - id: tail
                    type: alpha::footer
            """,
        )
        graph = load_graph(root)
        body = node_named(graph, "body")
        footer = node_named(graph, "footer")
        assert any(edge.source == body.id and edge.target == footer.id for edge in graph.edges)
        assert len({node.id for node in graph.nodes}) == len(graph.nodes)


    def test_nested_types_resolve_lexically(self):
        root = self.schema(
            "lexical.ksy",
            """
            meta: {id: lexical}
            types:
              parent:
                types:
                  child:
                    seq:
                      - id: item
                        type: sibling
                  sibling:
                    seq:
                      - id: value
                        type: u1
              sibling:
                seq:
                  - id: wrong
                    type: u1
            """,
        )
        graph = load_graph(root)
        child = node_named(graph, "child")
        siblings = [node for node in graph.nodes if node.title.split("::")[-1] == "sibling"]
        assert len(siblings) == 2
        lexical_sibling = next(node for node in siblings if "parent" in node.title)
        assert any(edge.source == child.id and edge.target == lexical_sibling.id for edge in graph.edges)


    def test_unresolved_custom_type_is_an_error(self):
        root = self.schema(
            "unresolved.ksy",
            "meta: {id: unresolved}\nseq:\n  - id: body\n    type: missing_type\n",
        )
        with self.assertRaisesRegex(SchemaError, "missing_type"):
            load_graph(root)

    def test_qualified_references_resolve_each_component_lexically(self):
        root = self.schema(
            "qualified.ksy",
            """
            meta: {id: qualified}
            seq:
              - id: sibling_body
                type: parent::child::sibling
              - id: parent_mode
                type: u1
                enum: parent::child::mode
            types:
              parent:
                types:
                  child: {}
                  sibling: {}
                enums:
                  mode: {0: idle}
            """,
        )
        graph = load_graph(root)
        node = node_named(graph, "qualified")
        sibling, mode = node_named(graph, "sibling"), node_named(graph, "mode")
        self.assertTrue(any(edge.source_row == "/seq/0" and edge.source == node.id and edge.target == sibling.id for edge in graph.edges))
        self.assertTrue(any(edge.source_row == "/seq/1" and edge.source == node.id and edge.target == mode.id for edge in graph.edges))

    def test_types_and_enums_are_visible_through_transitive_imports(self):
        root = self.schema(
            "outer.ksy",
            """
            meta: {id: outer, imports: [middle]}
            params:
              - id: entries
                type: leaf::entry[]
            seq:
              - id: state
                type: u1
                enum: leaf::state
            """,
        )
        self.schema("middle.ksy", "meta: {id: middle, imports: [leaf]}\n")
        self.schema("leaf.ksy", "meta: {id: leaf}\ntypes: {entry: {}}\nenums:\n  state: {0: idle}\n")
        graph = load_graph(root)
        outer, entry, state = (node_named(graph, name) for name in ("outer", "entry", "state"))
        self.assertTrue(any(edge.source == outer.id and edge.target == entry.id and edge.source_row == "/params/0" for edge in graph.edges))
        self.assertTrue(any(edge.source == outer.id and edge.target == state.id and edge.source_row == "/seq/0" for edge in graph.edges))


    def test_numeric_enum_keys_and_member_docs(self):
        root = self.schema(
            "enumerated.ksy",
            """
            meta: {id: enumerated}
            seq:
              - id: mode
                type: u1
                enum: mode
            enums:
              mode:
                0: idle
                255:
                  id: active
                  doc: 활성 상태입니다.
            """,
        )
        graph = load_graph(root)
        enum_node = node_named(graph, "mode")
        assert enum_node.kind == "enum"
        assert any(row.doc == "활성 상태입니다." for row in enum_node.rows)
        assert any("255" in f"{row.label} {row.type_label} {row.details}" for row in enum_node.rows)
        assert any(edge.target == enum_node.id for edge in graph.edges)


    def test_switch_cases_and_instance_types_create_references(self):
        root = self.schema(
            "switched.ksy",
            """
            meta: {id: switched}
            seq:
              - id: kind
                type: u1
              - id: body
                type:
                  switch-on: kind
                  cases:
                    1: first
                    _: second
            types:
              first:
                seq:
                  - id: value
                    type: u1
              second:
                seq:
                  - id: value
                    type: u1
            instances:
              preview:
                pos: 0
                type: first
                doc: 미리 읽은 첫 번째 레코드입니다.
            """,
        )
        graph = load_graph(root)
        root_node = node_named(graph, "switched")
        first = node_named(graph, "first")
        second = node_named(graph, "second")
        references = [edge for edge in graph.edges if edge.source == root_node.id]
        assert {first.id, second.id} <= {edge.target for edge in references}
        preview = next(row for row in root_node.rows if row.label == "preview")
        assert preview.doc == "미리 읽은 첫 번째 레코드입니다."
        assert any(edge.source_row == preview.id and edge.target == first.id for edge in references)


    def test_example_integrates_imports_switches_enums_instances_and_docs(self):
        graph = load_graph(self.examples_dir / "packet.ksy")
        assert graph.title
        assert len(graph.nodes) >= 6
        assert any(node.kind == "enum" for node in graph.nodes)
        assert any("메시지" in node.doc for node in graph.nodes)
        assert any("<tag>" in row.doc for node in graph.nodes for row in node.rows)
        assert any(row.label == "signature_copy" for node in graph.nodes for row in node.rows)
        ids = {node.id for node in graph.nodes}
        assert all(edge.source in ids and edge.target in ids for edge in graph.edges)


if __name__ == "__main__":
    unittest.main()
