"""Small, renderer-independent graph model for Kaitai schemas."""

from dataclasses import dataclass, field


@dataclass
class Row:
    id: str
    label: str
    type_label: str
    details: str = ""
    doc: str = ""
    kind: str = "field"
    pos_label: str = "—"
    size_label: str = "—"


@dataclass
class Node:
    id: str
    title: str
    subtitle: str = ""
    doc: str = ""
    rows: list[Row] = field(default_factory=list)
    kind: str = "type"
    source_file: str = ""
    size_label: str = ""


@dataclass
class Edge:
    source: str
    target: str
    source_row: str | None = None
    label: str = ""
    kind: str = "type"


@dataclass
class Graph:
    title: str
    nodes: list[Node]
    edges: list[Edge]
    root_source_file: str = ""
