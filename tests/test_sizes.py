from __future__ import annotations

import unittest

from helpers import SchemaTestCase

from kaitai_svg.loader import load_graph


def named(graph, name):
    return next(node for node in graph.nodes if node.title == name)


class SizeTests(SchemaTestCase):
    def test_fixed_nested_imports_and_contents_have_local_offsets(self):
        root = self.schema("root.ksy", """
            meta: {id: root, imports: [common]}
            seq:
              - {id: magic, contents: [65, 66, 0]}
              - {id: count, type: u2le}
              - {id: entry, type: common::entry}
            """)
        self.schema("common.ksy", """
            meta: {id: common}
            types:
              entry:
                seq:
                  - {id: tag, type: u1}
                  - {id: payload, size: 4}
            """)
        graph = load_graph(root)
        node = named(graph, "root")
        self.assertEqual(node.size_label, "10 B")
        self.assertEqual([(row.pos_label, row.size_label) for row in node.rows], [("0", "3 B"), ("3", "2 B"), ("5", "5 B")])
        self.assertEqual(named(graph, "common::entry").rows[1].pos_label, "1")

    def test_bit_offsets_and_byte_alignment_across_nested_types(self):
        root = self.schema("bits.ksy", """
            meta: {id: bits}
            seq:
              - {id: prefix, type: b3}
              - {id: child, type: fragment}
              - {id: byte, type: u1}
            types:
              fragment:
                seq:
                  - {id: first, type: b2}
                  - {id: rest, type: b3}
            """)
        graph = load_graph(root)
        node = named(graph, "bits")
        self.assertEqual([(row.pos_label, row.size_label) for row in node.rows], [("0", "3 bit"), ("0.3", "5 bit"), ("1", "1 B")])
        self.assertEqual(node.size_label, "2 B")
        fragment = named(graph, "bits::fragment")
        self.assertEqual(fragment.rows[1].pos_label, "0.2")
        self.assertEqual(fragment.size_label, "5 bit")
        root = self.schema("padding.ksy", "meta: {id: padding}\nseq: [{id: a, type: b6}, {id: b, type: u1}]\n")
        self.assertEqual([row.pos_label for row in load_graph(root).nodes[0].rows], ["0", "1"])

    def test_literal_and_symbolic_repetition_are_distinguished(self):
        root = self.schema("repeat.ksy", """
            meta: {id: repeat}
            seq:
              - {id: flags, type: b3, repeat: expr, repeat-expr: 3}
              - {id: values, type: u2, repeat: expr, repeat-expr: 4}
              - {id: records, type: u4, repeat: expr, repeat-expr: count}
              - {id: trailing, type: u1}
            """)
        node = load_graph(root).nodes[0]
        self.assertEqual([(row.pos_label, row.size_label) for row in node.rows], [("0", "9 bit"), ("2", "8 B"), ("10", "count × 4 B"), ("?", "1 B")])
        self.assertEqual(node.size_label, "?")

    def test_conditionals_and_instances_do_not_invent_fixed_sizes(self):
        root = self.schema("optional.ksy", """
            meta: {id: optional}
            params: [{id: enabled, type: bool}]
            seq:
              - {id: header, type: u1}
              - {id: body, type: u2, if: enabled}
              - {id: trailer, type: u1}
            instances:
              preview: {pos: 20, type: u4}
              total: {value: header + trailer}
            """)
        node = load_graph(root).nodes[0]
        self.assertEqual(node.size_label, "2–4 B")
        rows = {row.label: row for row in node.rows}
        self.assertEqual((rows["enabled"].pos_label, rows["enabled"].size_label), ("—", "—"))
        self.assertEqual(rows["body"].size_label, "0–2 B")
        self.assertEqual(rows["trailer"].pos_label, "1–3")
        self.assertEqual((rows["preview"].pos_label, rows["preview"].size_label), ("20", "4 B"))
        self.assertEqual((rows["total"].pos_label, rows["total"].size_label), ("—", "—"))

    def test_switch_requires_exhaustiveness_or_outer_size(self):
        root = self.schema("switches.ksy", """
            meta: {id: switches}
            seq:
              - id: complete
                type: {switch-on: tag, cases: {1: u2, _: u2}}
              - id: incomplete
                type: {switch-on: tag, cases: {1: u2, 2: u2}}
              - id: limited
                size: 5
                type: {switch-on: tag, cases: {1: u2}}
            """)
        node = load_graph(root).nodes[0]
        self.assertEqual([row.size_label for row in node.rows], ["2 B", "0–2 B", "5 B"])
        self.assertEqual(node.size_label, "7–9 B")

    def test_complementary_conditions_and_nested_size_do_not_inflate_ranges(self):
        root = self.schema("choice.ksy", """
            meta: {id: choice}
            params: [{id: use_alt, type: bool}]
            seq:
              - {id: normal, type: payload, if: not use_alt}
              - {id: alternate, type: payload, if: use_alt}
            types:
              payload:
                seq: [{id: data, type: b16}]
            """)
        node = load_graph(root).nodes[0]
        self.assertEqual(node.size_label, "2 B")
        self.assertEqual([row.size_label for row in node.rows if row.kind == "field"], ["0–2 B", "0–2 B"])

    def test_custom_repeats_account_for_alignment_per_iteration(self):
        root = self.schema("mixed.ksy", """
            meta: {id: mixed}
            seq:
              - {id: prefix, type: b3}
              - {id: entries, type: entry, repeat: expr, repeat-expr: 3}
            types:
              entry:
                seq: [{id: byte, type: u1}, {id: tail, type: b3}]
            """)
        node = load_graph(root).nodes[0]
        # Each repeated entry aligns before its byte: first begins at byte 1,
        # subsequent entries begin at bytes 3 and 5, ending at byte 6 + 3 bits.
        self.assertEqual(node.rows[1].pos_label, "1")
        self.assertEqual(node.rows[1].size_label, "43 bit")
        self.assertEqual(node.size_label, "51 bit")

    def test_shared_type_dag_is_memoized(self):
        lines = ["meta: {id: dag}", "seq: [{id: root, type: level_0}]", "types:"]
        for index in range(24):
            lines += [f"  level_{index}:", f"    seq: [{{id: a, type: level_{index + 1}}}, {{id: b, type: level_{index + 1}}}]"]
        lines += ["  level_24:", "    seq: [{id: data, type: u1}]"]
        node = load_graph(self.schema("dag.ksy", "\n".join(lines))).nodes[0]
        self.assertEqual(node.size_label, f"{2 ** 24} B")

    def test_recursive_and_terminated_types_remain_unknown(self):
        root = self.schema("recursive.ksy", """
            meta: {id: recursive, encoding: UTF-8}
            seq:
              - {id: name, type: strz}
              - {id: child, type: recursive, if: has_child}
            types:
              remainder:
                seq: [{id: rest, size-eos: true}]
            """)
        graph = load_graph(root)
        node = named(graph, "recursive")
        self.assertEqual(node.size_label, "?")
        self.assertEqual(node.rows[0].size_label, "?")
        self.assertEqual(node.rows[1].pos_label, "?")
        self.assertEqual(named(graph, "recursive::remainder").rows[0].size_label, "to EOS")

    def test_expression_text_is_preserved_without_evaluation(self):
        root = self.schema("expressions.ksy", """
            meta: {id: expressions}
            seq:
              - {id: computed, size: '2 + 3'}
              - {id: rest, type: u1}
            instances:
              preview: {pos: offset + 1, size: length}
            """)
        node = load_graph(root).nodes[0]
        self.assertEqual(node.rows[0].size_label, "2 + 3 B")
        self.assertEqual(node.rows[1].pos_label, "?")
        self.assertEqual(node.rows[2].pos_label, "offset + 1")
        self.assertEqual(node.rows[2].size_label, "length B")
        self.assertNotIn("size:", node.rows[0].details)
        self.assertNotIn("pos:", node.rows[2].details)

    def test_file_groups_disambiguate_equal_basenames_and_include_enums(self):
        root = self.schema("root.ksy", "meta: {id: root, imports: [left/common, right/common]}\n")
        self.schema("left/common.ksy", "meta: {id: left}\nenums: {mode: {0: off}}\n")
        self.schema("right/common.ksy", "meta: {id: right, ks-opaque-types: true}\nseq: [{id: item, type: external}]\n")
        graph = load_graph(root)
        self.assertEqual(named(graph, "root").source_file, "root.ksy")
        self.assertEqual(named(graph, "left::mode").source_file, "left/common.ksy")
        self.assertEqual(named(graph, "right").source_file, "right/common.ksy")
        self.assertEqual(named(graph, "external").source_file, "right/common.ksy")
        self.assertEqual(named(graph, "left::mode").rows[0].size_label, "—")


if __name__ == "__main__":
    unittest.main()
