"""Reproducible geometry comparison; does not write or replace any SVG files.

Run: uv run python tests/compare_routers.py examples/sqlite3.ksy
"""

from __future__ import annotations

import argparse
from itertools import combinations
import json
import math
from pathlib import Path
from time import perf_counter

from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Graph
from kaitai_svg.routing import ROUTERS

from test_router import bend_count, crossing_count, enters_rectangle, overlap_length


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schemas", type=Path, nargs="+")
    parser.add_argument("-I", "--import-path", type=Path, action="append", default=[])
    parser.add_argument("--show-imports", action="store_true")
    parser.add_argument("--show-nested-types", action="store_true")
    args = parser.parse_args()
    results = []
    for source in args.schemas:
        original = load_graph(source, args.import_path)
        hidden = set()
        if not args.show_imports:
            hidden.add("import")
        if not args.show_nested_types:
            hidden.add("contains")
        graph = Graph(original.title, original.nodes,
                      [edge for edge in original.edges if edge.kind not in hidden])
        for name in ROUTERS:
            start = perf_counter()
            layout = layout_graph(graph, router=name)
            seconds = perf_counter() - start
            paths = [edge.points for edge in layout.edges]
            results.append({
                "source": str(source), "router": name,
                "nodes": len(graph.nodes), "edges": len(graph.edges),
                "proper_crossings": sum(crossing_count(a, b) for a, b in combinations(paths, 2)),
                "shared_length": round(math.fsum(overlap_length(a, b) for a, b in combinations(paths, 2)), 3),
                "bends": sum(map(bend_count, paths)),
                "length": round(math.fsum(abs(a[0] - b[0]) + abs(a[1] - b[1])
                                          for path in paths for a, b in zip(path, path[1:])), 3),
                "node_collisions": sum(enters_rectangle(a, b, node)
                                       for path in paths for a, b in zip(path, path[1:])
                                       for node in layout.nodes),
                "seconds": round(seconds, 4),
            })
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
