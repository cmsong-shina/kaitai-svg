from __future__ import annotations

from contextlib import chdir, redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock
import unittest
import xml.etree.ElementTree as ET

from helpers import SchemaTestCase

from kaitai_svg import convert
from kaitai_svg.cli import main
from kaitai_svg.loader import load_graph


class CliTests(SchemaTestCase):
    def invoke(self, *arguments: str):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_svg(self, path):
        self.assertTrue(path.is_file(), path)
        self.assertEqual(ET.parse(path).getroot().tag, "{http://www.w3.org/2000/svg}svg")

    def test_recursive_glob_deduplicates_and_writes_beside_each_input(self):
        first = self.schema("first.ksy", "meta: {id: first}\n")
        second = self.schema("nested/second.ksy", "meta: {id: second}\n")
        with mock.patch("kaitai_svg.cli.load_graph", wraps=load_graph) as loader:
            status, _, errors = self.invoke(str(self.tmp_path / "**" / "*.ksy"), str(first))
        self.assertEqual(status, 0, errors)
        self.assertEqual(loader.call_count, 2)
        self.assert_svg(first.with_suffix(".svg"))
        self.assert_svg(second.with_suffix(".svg"))

    def test_multiple_patterns_and_paths_with_spaces(self):
        first = self.schema("folder with spaces/a.ksy", "meta: {id: first}\n")
        second = self.schema("other/b.ksy", "meta: {id: second}\n")
        status, _, errors = self.invoke(str(first.parent / "*.ksy"), str(second.parent / "*.ksy"))
        self.assertEqual(status, 0, errors)
        self.assert_svg(first.with_suffix(".svg"))
        self.assert_svg(second.with_suffix(".svg"))

    def test_out_dir_creates_directory_and_flattens_glob_outputs(self):
        first = self.schema("schemas/first.ksy", "meta: {id: first}\n")
        second = self.schema("schemas/nested/second.ksy", "meta: {id: second}\n")
        destination = self.tmp_path / "output folder" / "svgs"
        status, stdout, errors = self.invoke(
            "--out-dir", str(destination), str(first.parent / "**" / "*.ksy"), str(first),
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(stdout.splitlines(), [str(destination / "first.svg"),
                                               str(destination / "second.svg")])
        for source in (first, second):
            self.assert_svg(destination / source.with_suffix(".svg").name)
            self.assertFalse(source.with_suffix(".svg").exists())

    def test_out_dir_python_api_resolves_relative_path_and_preserves_import_lookup(self):
        source = self.schema("schemas/packet.ksy", "meta: {id: packet, imports: [common]}\n")
        self.schema("schemas/common.ksy", "meta: {id: common}\n")
        with chdir(self.tmp_path):
            output = convert(source, out_dir="output")
        self.assertEqual(output, self.tmp_path / "output" / "packet.svg")
        self.assert_svg(output)
        self.assertFalse(source.with_suffix(".svg").exists())

    def test_out_dir_expands_home_directory(self):
        source = self.schema("packet.ksy", "meta: {id: packet}\n")
        with mock.patch.dict("os.environ", {"HOME": str(self.tmp_path),
                                           "USERPROFILE": str(self.tmp_path)}):
            status, _, errors = self.invoke(str(source), "--out-dir", "~/output")
        self.assertEqual(status, 0, errors)
        self.assert_svg(self.tmp_path / "output" / "packet.svg")

    def test_out_dir_collision_preserves_output_and_processes_other_inputs(self):
        first = self.schema("first/packet.ksy", "meta: {id: first}\n")
        second = self.schema("second/packet.ksy", "meta: {id: second}\n")
        valid = self.schema("valid.ksy", "meta: {id: valid}\n")
        existing = self.schema("output/packet.svg", "previous output")
        status, _, errors = self.invoke(str(first), str(second), str(valid),
                                        "--out-dir", str(existing.parent))
        self.assertEqual(status, 1)
        self.assertIn("collision", errors)
        self.assertIn(str(first), errors)
        self.assertIn(str(second), errors)
        self.assertEqual(existing.read_text(encoding="utf-8"), "previous output")
        self.assert_svg(existing.parent / "valid.svg")

    def test_out_dir_file_reports_error(self):
        source = self.schema("packet.ksy", "meta: {id: packet}\n")
        destination = self.schema("output", "existing file")
        status, _, errors = self.invoke(str(source), "--out-dir", str(destination))
        self.assertEqual(status, 1)
        self.assertIn("output", errors)
        self.assertEqual(destination.read_text(encoding="utf-8"), "existing file")
        self.assertFalse(source.with_suffix(".svg").exists())

    def test_out_dir_invalid_schema_does_not_create_directory(self):
        source = self.schema("broken.ksy", "meta: {id: broken}\nseq: invalid\n")
        destination = self.tmp_path / "output"
        status, _, _ = self.invoke(str(source), "--out-dir", str(destination))
        self.assertEqual(status, 1)
        self.assertFalse(destination.exists())

    def test_out_dir_failed_replace_preserves_output_and_cleans_temporary(self):
        source = self.schema("packet.ksy", "meta: {id: packet}\n")
        output = self.schema("output/packet.svg", "previous output")
        with mock.patch("kaitai_svg.cli.os.replace", side_effect=PermissionError("locked")):
            status, _, errors = self.invoke(str(source), "--out-dir", str(output.parent))
        self.assertEqual(status, 1)
        self.assertIn("locked", errors)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(list(output.parent.iterdir()), [output])

    def test_unicode_input_directory_and_filename(self):
        source = self.schema("한글 폴더/패킷.ksy", "meta: {id: packet}\n")
        status, _, errors = self.invoke(str(source))
        self.assertEqual(status, 0, errors)
        self.assert_svg(source.with_suffix(".svg"))

    def test_invalid_file_does_not_stop_valid_files(self):
        invalid = self.schema("bad.ksy", "meta: {id: bad}\nseq: invalid\n")
        valid = self.schema("valid.ksy", "meta: {id: valid}\n")
        status, _, errors = self.invoke(str(invalid), str(valid))
        self.assertEqual(status, 1)
        self.assertIn("bad.ksy", errors)
        self.assertFalse(invalid.with_suffix(".svg").exists())
        self.assert_svg(valid.with_suffix(".svg"))

    def test_unmatched_pattern_reports_failure_but_processes_other_inputs(self):
        valid = self.schema("valid.ksy", "meta: {id: valid}\n")
        status, _, errors = self.invoke(str(self.tmp_path / "missing*.ksy"), str(valid))
        self.assertEqual(status, 1)
        self.assertIn("missing", errors)
        self.assert_svg(valid.with_suffix(".svg"))

    def test_invalid_schema_preserves_existing_output(self):
        invalid = self.schema("broken.ksy", "meta: {id: broken}\nseq: invalid\n")
        output = invalid.with_suffix(".svg")
        output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        status, _, _ = self.invoke(str(invalid))
        self.assertEqual(status, 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_failed_atomic_replace_preserves_existing_output_and_cleans_temporary(self):
        source = self.schema("packet.ksy", "meta: {id: packet}\n")
        output = source.with_suffix(".svg")
        output.write_text("previous output", encoding="utf-8")
        before = set(self.tmp_path.iterdir())
        with mock.patch("kaitai_svg.cli.os.replace", side_effect=PermissionError("output is locked")):
            status, _, errors = self.invoke(str(source))
        self.assertEqual(status, 1)
        self.assertIn("output is locked", errors)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(set(self.tmp_path.iterdir()), before)

    def test_literal_filename_with_glob_characters(self):
        source = self.schema("packet[1].ksy", "meta: {id: packet}\n")
        status, _, errors = self.invoke(str(source))
        self.assertEqual(status, 0, errors)
        self.assert_svg(source.with_suffix(".svg"))

    def test_library_import_option(self):
        root = self.schema(
            "packet.ksy",
            "meta: {id: packet, imports: [/common]}\nseq:\n  - id: body\n    type: common\n",
        )
        self.schema("libraries/common.ksy", "meta: {id: common}\nseq:\n  - id: value\n    type: u1\n")
        status, _, errors = self.invoke(str(root), "-I", str(self.tmp_path / "libraries"))
        self.assertEqual(status, 0, errors)
        self.assert_svg(root.with_suffix(".svg"))

    def test_generated_svg_contains_korean_documentation(self):
        root = self.schema(
            "documented.ksy",
            "meta: {id: documented}\ndoc: 한글 설명입니다.\nseq:\n  - id: value\n    type: u1\n    doc: 값에 대한 설명입니다.\n",
        )
        status, _, errors = self.invoke(str(root))
        self.assertEqual(status, 0, errors)
        content = root.with_suffix(".svg").read_text(encoding="utf-8")
        self.assertIn("한글 설명입니다.", content)
        self.assertIn("값에 대한 설명입니다.", content)

    def test_import_lines_are_opt_in_for_cli_and_python_api(self):
        root = self.schema(
            "packet.ksy",
            "meta: {id: packet, imports: [common]}\nseq:\n  - id: body\n    type: common\n",
        )
        self.schema("common.ksy", "meta: {id: common}\n")
        for use_api in (False, True):
            # Turning the option back off must replace the previous SVG too.
            for show_imports in (False, True, False):
                with self.subTest(use_api=use_api, show_imports=show_imports):
                    if use_api:
                        options = {"show_imports": True} if show_imports else {}
                        convert(root, **options)
                    else:
                        options = ["--show-imports"] if show_imports else []
                        status, _, errors = self.invoke(str(root), *options)
                        self.assertEqual(status, 0, errors)
                    xml = ET.parse(root.with_suffix(".svg")).getroot()
                    groups = list(xml.iter("{http://www.w3.org/2000/svg}g"))
                    imports = [g for g in groups if "edge-kind-import" in g.get("class", "").split()]
                    self.assertEqual(len(imports), 2 if show_imports else 0)  # edge + legend
                    self.assertEqual(sum("node" in g.get("class", "").split() for g in groups), 2)
                    self.assertEqual(sum("edge-kind-type" in g.get("class", "").split() for g in groups), 1)

    def test_nested_lines_are_independent_opt_in_for_cli_and_python_api(self):
        root = self.schema(
            "packet.ksy",
            "meta: {id: packet, imports: [common]}\n"
            "seq:\n  - {id: body, type: inner}\n  - {id: shared, type: common}\n"
            "types:\n  inner:\n    seq:\n      - {id: value, type: u1}\n",
        )
        self.schema("common.ksy", "meta: {id: common}\n")
        for use_api in (False, True):
            for imports, nested in ((False, False), (False, True), (True, False), (True, True), (False, False)):
                with self.subTest(use_api=use_api, imports=imports, nested=nested):
                    if use_api:
                        options = {}
                        if imports:
                            options["show_imports"] = True
                        if nested:
                            options["show_nested_types"] = True
                        convert(root, **options)
                    else:
                        options = (["--show-imports"] if imports else [])
                        if nested:
                            options.append("--show-nested-types")
                        status, _, errors = self.invoke(str(root), *options)
                        self.assertEqual(status, 0, errors)
                    groups = list(ET.parse(root.with_suffix(".svg")).getroot()
                                  .iter("{http://www.w3.org/2000/svg}g"))
                    for kind, enabled in (("import", imports), ("contains", nested)):
                        self.assertEqual(sum(f"edge-kind-{kind}" in g.get("class", "").split() for g in groups),
                                         2 if enabled else 0)  # edge + legend
                    self.assertEqual(sum("node" in g.get("class", "").split() for g in groups), 3)
                    self.assertEqual(sum("edge-kind-type" in g.get("class", "").split() for g in groups), 2)

    def test_interactive_is_independent_opt_in_for_cli_and_python_api(self):
        root = self.schema(
            "packet.ksy",
            "meta: {id: packet, imports: [common]}\n"
            "seq:\n  - {id: body, type: inner, doc: Nested body}\n  - {id: shared, type: common}\n"
            "types:\n  inner:\n    seq:\n      - {id: value, type: u1}\n",
        )
        self.schema("common.ksy", "meta: {id: common}\n")
        svg = "{http://www.w3.org/2000/svg}"
        for use_api in (False, True):
            for imports, nested in ((False, False), (False, True), (True, False), (True, True)):
                for interactive in (None, True, False):
                    with self.subTest(use_api=use_api, imports=imports, nested=nested,
                                      interactive=interactive):
                        if use_api:
                            options = {"show_imports": imports, "show_nested_types": nested}
                            if interactive is not None:
                                options["interactive"] = interactive
                            convert(root, **options)
                        else:
                            options = ["--show-imports"] if imports else []
                            if nested:
                                options.append("--show-nested-types")
                            if interactive:
                                options.append("--interactive")
                            status, _, errors = self.invoke(str(root), *options)
                            self.assertEqual(status, 0, errors)
                        xml = ET.parse(root.with_suffix(".svg")).getroot()
                        self.assertEqual(xml.find(svg + "script") is not None, bool(interactive))
                        self.assertEqual(bool(list(xml.iter(svg + "foreignObject"))), bool(interactive))
                        groups = list(xml.iter(svg + "g"))
                        self.assertEqual(any(g.get("id") == "controls" for g in groups), bool(interactive))
                        for kind, enabled in (("import", imports), ("contains", nested)):
                            self.assertEqual(sum(f"edge-kind-{kind}" in g.get("class", "").split()
                                                 for g in groups), 2 if enabled else 0)
                        self.assertEqual(sum("node" in g.get("class", "").split() for g in groups), 3)
                        self.assertEqual(sum("edge-kind-type" in g.get("class", "").split()
                                             for g in groups), 2)
                        self.assertTrue(any("Nested body" in (title.text or "")
                                            for title in xml.iter(svg + "title")))


if __name__ == "__main__":
    unittest.main()
