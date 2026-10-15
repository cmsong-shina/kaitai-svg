"""Compare complete routed layouts as JSON without writing SVG files.

Run: uv run python tests/compare_layouts.py examples/vlq_base128_be.ksy
Use --router default to compare placement with the legacy router instead.
"""

from __future__ import annotations

import argparse
from itertools import combinations
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

from kaitai_svg.geometry import measure_paths, path_segments
from kaitai_svg.layout import layout_graph
from kaitai_svg.loader import SchemaError, load_graph
from kaitai_svg.model import Graph
from kaitai_svg.placement import LAYOUTS
from kaitai_svg.routing import ROUTERS
from kaitai_svg.visibility import filter_hidden, filter_unused_imports


def filtered_graph(source, args):
    """Apply CLI path selectors and the same filtering order as render_svg."""
    source = source.resolve()
    graph = load_graph(source, args.import_path)
    selectors = []
    for selector in args.hide:
        candidate = Path(selector).expanduser()
        has_directory = "/" in selector or "\\" in selector
        if candidate.suffix.lower() == ".ksy" and (
            candidate.is_absolute() or (has_directory and candidate.is_file())
        ):
            try:
                selector = "./" + Path(os.path.relpath(candidate.resolve(), source.parent)).as_posix()
            except ValueError:  # An import can be on another Windows drive.
                selector = candidate.resolve().as_posix()
        selectors.append(selector)
    graph = filter_hidden(graph, selectors)
    if args.treeshake:
        graph = filter_unused_imports(graph)
    hidden_kinds = set()
    if not args.show_imports:
        hidden_kinds.add("import")
    if not args.show_nested_types:
        hidden_kinds.add("contains")
    return Graph(graph.title, graph.nodes,
                 [edge for edge in graph.edges if edge.kind not in hidden_kinds],
                 graph.root_source_file)


def overlaps(first, second):
    """Count interior overlap; shared rectangle boundaries are valid."""
    return (first.x < second.x + second.width and second.x < first.x + first.width
            and first.y < second.y + second.height and second.y < first.y + first.height)


def enters_rectangle(a, b, rectangle):
    left, top = rectangle.x, rectangle.y
    right, bottom = left + rectangle.width, top + rectangle.height
    if a[0] == b[0]:
        return (left < a[0] < right
                and max(min(a[1], b[1]), top) < min(max(a[1], b[1]), bottom))
    if a[1] == b[1]:
        return (top < a[1] < bottom
                and max(min(a[0], b[0]), left) < min(max(a[0], b[0]), right))
    # A nonorthogonal path is reported separately; the routers produce
    # horizontal/vertical paths, for which these collision checks are exact.
    return False


def geometry_counters(drawing):
    segments = [segment for route in drawing.edges for segment in path_segments(route.points)]
    points = [point for route in drawing.edges for point in route.points]
    # Match the router's protected file-header bands, including wrapped titles.
    captions = [SimpleNamespace(x=group.x + 16, y=group.y + 8,
                                width=group.width - 32, height=group.header_height - 16)
                for group in drawing.groups]

    def inside_canvas(rectangle):
        return (0 <= rectangle.x <= rectangle.x + rectangle.width <= drawing.width
                and 0 <= rectangle.y <= rectangle.y + rectangle.height <= drawing.height)

    by_id = {box.node.id: box for box in drawing.nodes}
    containment_errors = 0
    for group in drawing.groups:
        for node_id in group.node_ids:
            box = by_id[node_id]
            if not (group.x <= box.x <= box.x + box.width <= group.x + group.width
                    and group.y + group.header_height <= box.y
                    and box.y + box.height <= group.y + group.height):
                containment_errors += 1
    return {
        "node_overlaps": sum(overlaps(a, b) for a, b in combinations(drawing.nodes, 2)),
        "group_overlaps": sum(overlaps(a, b) for a, b in combinations(drawing.groups, 2)),
        "node_collisions": sum(enters_rectangle(a, b, node)
                               for a, b in segments for node in drawing.nodes),
        "caption_collisions": sum(enters_rectangle(a, b, caption)
                                  for a, b in segments for caption in captions),
        "group_containment_errors": containment_errors,
        "out_of_bounds_nodes": sum(not inside_canvas(node) for node in drawing.nodes),
        "out_of_bounds_groups": sum(not inside_canvas(group) for group in drawing.groups),
        "out_of_bounds_points": sum(not (0 <= x <= drawing.width and 0 <= y <= drawing.height)
                                    for x, y in points),
        "non_finite_points": sum(not (math.isfinite(x) and math.isfinite(y)) for x, y in points),
        "non_orthogonal_segments": sum(a[0] != b[0] and a[1] != b[1] for a, b in segments),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schemas", type=Path, nargs="+")
    parser.add_argument("-I", "--import-path", type=Path, action="append", default=[])
    parser.add_argument("--router", choices=tuple(ROUTERS), default="orthogonal")
    parser.add_argument("--layout-width", type=int,
                        help="width budget for the vertical layout; other layouts keep their existing sizing")
    parser.add_argument("--show-imports", action="store_true")
    parser.add_argument("--show-nested-types", action="store_true")
    parser.add_argument("--hide", action="append", default=[], metavar="ID|FILENAME")
    parser.add_argument("--treeshake", action="store_true")
    args = parser.parse_args()
    results = []
    for source in args.schemas:
        try:
            graph = filtered_graph(source, args)
            for name in LAYOUTS:
                start = perf_counter()
                drawing = layout_graph(graph, router=args.router, layout=name,
                                       layout_width=args.layout_width if LAYOUTS[name].web_vertical else None)
                seconds = perf_counter() - start
                metrics = measure_paths([edge.points for edge in drawing.edges])
                results.append({
                    "source": str(source), "layout": name, "router": args.router,
                    "nodes": len(graph.nodes), "edges": len(graph.edges),
                    "proper_crossings": metrics.crossings,
                    "shared_length": round(metrics.shared_length, 3),
                    "bends": metrics.bends, "length": round(metrics.length, 3),
                    "width": round(drawing.width, 3), "height": round(drawing.height, 3),
                    "area": round(drawing.width * drawing.height, 3),
                    **geometry_counters(drawing), "seconds": round(seconds, 4),
                })
        except (SchemaError, OSError, ValueError, RecursionError) as exc:
            print(f"error: {source}: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
