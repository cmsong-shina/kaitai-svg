from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest


class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp_path = Path(temporary.name)
        self.examples_dir = Path(__file__).resolve().parent / "fixtures"

    def schema(self, name: str, source: str) -> Path:
        """Write a real schema under an isolated import root."""
        path = self.tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        return path
