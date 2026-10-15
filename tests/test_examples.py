"""Smoke checks for schemas shipped as public examples."""

from __future__ import annotations

import shutil
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from helpers import SchemaTestCase

from kaitai_svg.cli import convert_file
from kaitai_svg.loader import load_graph


ROOT = Path(__file__).resolve().parents[1]
SVG = "{http://www.w3.org/2000/svg}"


class BundledExampleTests(SchemaTestCase):
    def test_sqlite_example_converts_with_its_relative_import(self):
        for name in ("sqlite3.ksy", "vlq_base128_be.ksy"):
            shutil.copyfile(ROOT / "examples" / name, self.tmp_path / name)
        source = self.tmp_path / "sqlite3.ksy"

        graph = load_graph(source)
        self.assertIn("vlq_base128_be.ksy", {node.source_file for node in graph.nodes})
        self.assertTrue(any(edge.kind == "type" for edge in graph.edges))

        output = convert_file(source)
        static = ET.parse(output).getroot()
        self.assertEqual(static.find(SVG + "title").text, "SQLite3 database file")
        self.assertIsNone(static.find(SVG + "script"))

        convert_file(source, interactive=True)
        interactive = ET.parse(output).getroot()
        self.assertIsNotNone(interactive.find(SVG + "script"))
        self.assertTrue(any(element.get("id") == "controls" for element in interactive.iter(SVG + "g")))


if __name__ == "__main__":
    unittest.main()
