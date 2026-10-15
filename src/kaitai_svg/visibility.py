"""Select definitions and rows to omit before diagram layout."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import os
from pathlib import Path
import posixpath

from .model import Graph


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
    return Graph(graph.title, nodes, edges)
