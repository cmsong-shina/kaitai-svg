"""Cross-platform command line interface and atomic output writing."""

from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path
import sys
import tempfile
from collections.abc import Sequence

from . import __version__
from .loader import SchemaError, load_graph
from .render import render_svg
from .routing import ROUTERS, Router


def expand_inputs(patterns: Sequence[str]) -> tuple[list[Path], list[str]]:
    """Expand quoted globs on every OS and preserve deterministic input order."""
    paths: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for pattern in patterns:
        expanded = os.path.expanduser(pattern)
        literal = Path(expanded)
        matches = [str(literal)] if literal.is_file() else sorted(
            glob.glob(expanded, recursive=True)
        )
        files = [Path(match) for match in matches if Path(match).is_file()]
        if not files:
            errors.append(f"No files match: {pattern}")
        for path in files:
            if path.suffix.lower() != ".ksy":
                errors.append(f"Expected a .ksy schema: {path}")
                continue
            canonical = path.resolve()
            if canonical not in seen:
                seen.add(canonical)
                paths.append(canonical)
    return paths, errors


def convert_file(path: str | Path, import_paths: Sequence[str | Path] = (), *,
                 show_imports: bool = False, show_nested_types: bool = False,
                 interactive: bool = False, color_type_edges: bool = False,
                 router: str | type[Router] = "default",
                 hide: Sequence[str | Path] = (), treeshake: bool = False) -> Path:
    """Validate all dependencies before atomically replacing the sibling SVG."""
    source = Path(path).resolve()
    if source.suffix.lower() != ".ksy":
        raise SchemaError(f"Expected a .ksy schema: {source}")
    graph = load_graph(source, [Path(item) for item in import_paths])
    # Source labels are relative to the input's directory. Also accept paths
    # supplied from the working directory, including absolute filenames.
    selectors = []
    for value in hide:
        selector = os.fspath(value)
        candidate = Path(selector).expanduser()
        has_directory = "/" in selector or "\\" in selector
        if candidate.suffix.lower() == ".ksy" and (
            candidate.is_absolute() or (has_directory and candidate.is_file())
        ):
            try:
                selector = "./" + Path(os.path.relpath(candidate.resolve(), source.parent)).as_posix()
            except ValueError:  # A Windows import may live on another drive.
                selector = candidate.resolve().as_posix()
        selectors.append(selector)
    svg = render_svg(graph, show_imports=show_imports, show_nested_types=show_nested_types,
                     interactive=interactive, color_type_edges=color_type_edges,
                     router=router, hide=selectors, treeshake=treeshake)
    output = source.with_suffix(".svg")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=source.parent,
            prefix=f".{source.stem}.", suffix=".svg.tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(svg)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kaitai-svg",
        description="Validate Kaitai Struct schemas and create static SVGs beside them, with optional interactivity.",
        epilog='Example: kaitai-svg "schemas/**/*.ksy" -I ./format-library',
    )
    parser.add_argument("patterns", nargs="+", help=".ksy files or glob patterns (quote globs)")
    parser.add_argument("-I", "--import-path", action="append", default=[], metavar="DIR",
                        help="import search directory; repeat for multiple directories")
    parser.add_argument("--show-imports", action="store_true",
                        help="draw import connections (hidden by default; imported types are always included)")
    parser.add_argument("--show-nested-types", action="store_true",
                        help="draw nested definition connections (hidden by default; nested types are always included)")
    parser.add_argument("--interactive", action="store_true",
                        help="enable documentation tooltips, highlighting, zoom and pan (static SVG by default)")
    parser.add_argument("--color-type-edges", action="store_true",
                        help="give each type connection a distinct high-contrast color; reuse colors after 12")
    parser.add_argument("--router", choices=tuple(ROUTERS), default="default",
                        help="edge routing algorithm: default preserves existing routes; orthogonal optimizes crossings, shared paths and bends")
    parser.add_argument("--hide", action="append", default=[], metavar="ID|FILENAME",
                        help="exclude an element ID (optionally qualified with ::) or all elements from a .ksy file; repeat for multiple exclusions")
    parser.add_argument("--treeshake", action="store_true",
                        help="omit imported types and enums unused by the input schema and its referenced definitions")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    paths, errors = expand_inputs(args.patterns)
    for message in errors:
        print(f"error: {message}", file=sys.stderr)
    failed = bool(errors)
    for path in paths:
        try:
            output = convert_file(path, args.import_path, show_imports=args.show_imports,
                                  show_nested_types=args.show_nested_types, interactive=args.interactive,
                                  color_type_edges=args.color_type_edges, router=args.router,
                                  hide=args.hide, treeshake=args.treeshake)
        except (SchemaError, OSError, ValueError, RecursionError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            failed = True
        else:
            print(output)
    return 1 if failed else 0
