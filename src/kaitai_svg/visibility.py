"""Select definitions and rows to omit before diagram layout."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import os
from pathlib import Path
import posixpath
import re

from .model import Graph


_QUALIFIED_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:::[A-Za-z_][A-Za-z_0-9]*)+")


def _filename(value: str) -> str:
    return os.path.normcase(posixpath.normpath(value.replace("\\", "/")))


def _matches_id(name: str, selectors: set[str]) -> bool:
    parts = name.split("::")
    return any("::".join(parts[index:]) in selectors for index in range(len(parts)))


def filter_hidden(graph: Graph, hide: Sequence[str | Path]) -> Graph:
    """Omit matching IDs, KSY source files and their incident connections.

    IDs may be local or qualified with ``::``; generated graph IDs also work.
    File selectors match source paths or, without a directory, all basenames.
    The graph and its size annotations remain unchanged.
    """
    identifiers: set[str] = set()
    filenames: set[str] = set()
    basenames: set[str] = set()
    for value in hide:
        selector = os.fspath(value)
        if selector.lower().endswith(".ksy"):
            filenames.add(_filename(selector))
            if "/" not in selector and "\\" not in selector:
                basenames.add(_filename(selector))
        else:
            identifiers.add(selector)
    if not identifiers and not filenames:
        return graph

    nodes = []
    hidden_nodes: set[str] = set()
    hidden_rows: set[tuple[str, str]] = set()
    for node in graph.nodes:
        source = _filename(node.source_file)
        basename = _filename(posixpath.basename(node.source_file.replace("\\", "/")))
        if (node.id in identifiers or _matches_id(node.title, identifiers)
                or source in filenames or basename in basenames):
            hidden_nodes.add(node.id)
            continue
        rows = []
        for row in node.rows:
            if row.id in identifiers or _matches_id(f"{node.title}::{row.label}", identifiers):
                hidden_rows.add((node.id, row.id))
            else:
                rows.append(row)
        nodes.append(replace(node, rows=rows) if len(rows) != len(node.rows) else node)

    edges = [edge for edge in graph.edges
             if edge.source not in hidden_nodes and edge.target not in hidden_nodes
             and (edge.source, edge.source_row) not in hidden_rows]
    return Graph(graph.title, nodes, edges, graph.root_source_file)


def filter_unused_imports(graph: Graph) -> Graph:
    """Keep imported definitions reachable from the input file's definitions.

    Type and enum references determine use. Containing definitions are kept
    for context along with the definitions that their own fields reference.
    """
    if not graph.nodes:
        return graph
    root_file = graph.root_source_file or graph.nodes[0].source_file
    if not root_file:
        return graph

    references: dict[str, list[str]] = {}
    parents: dict[str, str] = {}
    definitions: dict[str, set[str]] = {}
    for node in graph.nodes:
        if node.kind == "opaque":
            continue
        parts = node.title.split("::")
        for index in range(len(parts)):
            definitions.setdefault("::".join(parts[index:]), set()).add(node.id)
    for edge in graph.edges:
        if edge.kind in {"type", "enum"}:
            references.setdefault(edge.source, []).append(edge.target)
        elif edge.kind == "contains":
            parents[edge.target] = edge.source
    # Explicitly qualified names in expressions can use enum members without
    # a field's `enum` property. Retain matching definitions conservatively.
    for node in graph.nodes:
        expressions = [node.subtitle]
        for row in node.rows:
            expressions.extend((row.type_label, row.details))
        for expression in expressions:
            for match in _QUALIFIED_NAME.finditer(expression):
                parts = match.group().split("::")
                for count in (len(parts), len(parts) - 1):
                    references.setdefault(node.id, []).extend(
                        definitions.get("::".join(parts[:count]), ())
                    )

    live = {node.id for node in graph.nodes if node.source_file == root_file}
    pending = list(live)
    while pending:
        source = pending.pop()
        targets = references.get(source, ())
        parent = parents.get(source)
        if parent is not None:
            targets = [*targets, parent]
        for target in targets:
            if target not in live:
                live.add(target)
                pending.append(target)

    return Graph(graph.title, [node for node in graph.nodes if node.id in live],
                 [edge for edge in graph.edges if edge.source in live and edge.target in live],
                 graph.root_source_file)
