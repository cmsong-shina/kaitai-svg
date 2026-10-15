"""Load KSY files, validate their syntax, and resolve a diagram's type graph.

The upstream JSON schema is bundled so ordinary rendering never uses the
network. This is a schema validator and a reference resolver, not a Kaitai
expression compiler: expressions are displayed as written, never evaluated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft7Validator
import yaml

from .model import Edge, Graph, Node, Row
from .sizes import annotate_sizes


class SchemaError(ValueError):
    """An invalid schema, unresolved reference, or unavailable import."""


class _KsyLoader(yaml.SafeLoader):
    """Safe YAML with duplicate detection and YAML 1.2 boolean spelling."""

    yaml_implicit_resolvers = {
        key: [
            (tag, pattern)
            for tag, pattern in rules
            if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")
        ]
        for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        explicit = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool, type(None))):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "mapping keys must be scalar values", key_node.start_mark,
                )
            name = _text(key)
            if name in explicit:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key {key!r}", key_node.start_mark,
                )
            explicit.add(name)
        # YAML merge keys intentionally permit an explicit value to override
        # an inherited one. Check explicit duplicates before flattening them.
        self.flatten_mapping(node)
        result: dict = {}
        for key_node, value_node in node.value:
            key = _text(self.construct_object(key_node, deep=deep))
            result[key] = self.construct_object(value_node, deep=deep)
        return result


_KsyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def _json_value(value: Any, active: set[int] | None = None) -> Any:
    """JSON Schema needs string mapping keys, including enum/case numbers."""
    if active is None:
        active = set()
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise SchemaError("recursive YAML aliases are not supported")
        active.add(id(value))
        try:
            if isinstance(value, list):
                return [_json_value(item, active) for item in value]
            result = {}
            for key, item in value.items():
                if not isinstance(key, (str, int, float, bool, type(None))):
                    raise SchemaError("mapping keys must be strings or scalar values")
                name = _text(key)
                if name in result:
                    raise SchemaError(f"duplicate key after JSON conversion: {name!r}")
                result[name] = _json_value(item, active)
            return result
        finally:
            active.remove(id(value))
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaError("non-finite YAML numbers are not valid JSON values")
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise SchemaError(f"unsupported YAML value: {type(value).__name__}")
    return value


@lru_cache(maxsize=1)
def _validator() -> Draft7Validator:
    resource = files("kaitai_svg").joinpath("resources", "ksy_schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _pointer(parts: Sequence[Any]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            data = _json_value(yaml.load(stream, Loader=_KsyLoader))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError, SchemaError) as exc:
        raise SchemaError(f"{path}: {exc}") from exc
    errors = sorted(
        _validator().iter_errors(data), key=lambda error: _pointer(error.absolute_path)
    )
    if errors:
        lines = [f"{_pointer(error.absolute_path)}: {error.message}" for error in errors[:8]]
        if len(errors) > 8:
            lines.append(f"... and {len(errors) - 8} more validation errors")
        raise SchemaError(f"{path}: invalid KSY (official JSON Schema)\n" + "\n".join(lines))
    # Draft 7 ignores siblings of $ref, including the upstream meta.required.
    # A top-level type nevertheless needs a name for import/type resolution.
    if not isinstance(data.get("meta"), dict) or "id" not in data["meta"]:
        raise SchemaError(f"{path}: /meta/id: a top-level schema must declare meta.id")
    data["meta"]["id"] = _text(data["meta"]["id"])
    return data


@dataclass
class _Document:
    path: Path
    data: dict[str, Any]
    imports: list[_Document] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.data["meta"]["id"]


@dataclass
class _Scope:
    document: _Document
    names: tuple[str, ...]
    spec: dict[str, Any]
    node: Node
    meta: dict[str, Any]
    parent: _Scope | None = None


_PRIMITIVE = re.compile(r"(?:[us](?:1|[248](?:le|be)?)|f[48](?:le|be)?|b[0-9]+(?:le|be)?|strz?)\Z")
_PARAM_PRIMITIVE_PATTERN = re.compile(r"(?:[us][1248]|f[48]|b[0-9]+(?:le|be)?|str)\Z")
_PARAM_PRIMITIVES = {"bytes", "bool", "struct", "io", "any"}


class _GraphBuilder:
    def __init__(self, import_paths: Sequence[Path]):
        configured = [Path(item).expanduser().resolve() for item in import_paths]
        configured += [Path(item).expanduser().resolve() for item in os.environ.get("KSPATH", "").split(os.pathsep) if item]
        self.import_paths = list(dict.fromkeys(configured))
        self.documents: dict[Path, _Document] = {}
        self.scopes: dict[tuple[Path, tuple[str, ...]], _Scope] = {}
        self.enums: dict[tuple[Path, tuple[str, ...]], Node] = {}
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.opaque: dict[tuple[Path, str], Node] = {}
        self.root_directory: Path | None = None

    def source_file(self, path: Path) -> str:
        """Keep same-basename imports distinct without exposing absolute paths."""
        try:
            return Path(os.path.relpath(path, self.root_directory)).as_posix()
        except ValueError:  # Windows imports can live on another drive.
            return path.as_posix()

    def error(self, scope: _Scope, location: str, message: str) -> SchemaError:
        nested = "".join(f"/types/{name}" for name in scope.names)
        return SchemaError(f"{scope.document.path}: {nested}{location}: {message}")

    def import_file(self, importer: Path, name: str) -> Path:
        relative = Path(*name.lstrip("/").split("/"))
        relative = relative.parent / (relative.name + ".ksy")
        candidates = (
            [root / relative for root in self.import_paths]
            if name.startswith("/") else [importer.parent / relative]
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        tried = ", ".join(str(item) for item in candidates) or "no library paths configured; use -I or KSPATH"
        raise SchemaError(f"{importer}: import {name!r} not found (searched: {tried})")

    def load(self, path: Path) -> _Document:
        """Iterative import traversal permits arbitrarily long import cycles."""
        path = path.expanduser().resolve()
        if path in self.documents:
            return self.documents[path]
        root = _Document(path, _read_schema(path))
        if self.root_directory is None:
            self.root_directory = path.parent
        self.documents[path] = root
        pending = [root]
        while pending:
            document = pending.pop()
            specs = [document.data]
            while specs:
                spec = specs.pop()
                names = spec.get("meta", {}).get("imports", [])
                if isinstance(names, str):
                    names = [names]
                for name in names:
                    target = self.import_file(document.path, name)
                    if target not in self.documents:
                        imported = _Document(target, _read_schema(target))
                        self.documents[target] = imported
                        pending.append(imported)
                    imported = self.documents[target]
                    if all(item.path != target for item in document.imports):
                        document.imports.append(imported)
                specs.extend(spec.get("types", {}).values())
        return root

    def add_node(self, **kwargs: Any) -> Node:
        node = Node(id=f"node-{len(self.nodes)}", **kwargs)
        self.nodes.append(node)
        return node

    def index(self) -> None:
        for document in self.documents.values():
            pending = [((), document.data, None)]
            while pending:
                names, spec, parent = pending.pop()
                meta = {**(parent.meta if parent else {}), **spec.get("meta", {})}
                settings = [f"{name}: {_text(meta[name])}" for name in ("endian", "bit-endian", "encoding") if name in meta]
                subtitle = " · ".join([document.path.name, *settings])
                title = "::".join((document.name, *names))
                node = self.add_node(title=title, subtitle=subtitle, doc=spec.get("doc", ""), source_file=self.source_file(document.path))
                scope = _Scope(document, names, spec, node, meta, parent)
                self.scopes[document.path, names] = scope
                if parent:
                    self.edges.append(Edge(parent.node.id, node.id, label="nested type", kind="contains"))
                for name, values in spec.get("enums", {}).items():
                    enum = self.add_node(title=f"{title}::{name}", subtitle="enum", kind="enum", source_file=self.source_file(document.path))
                    self.enums[document.path, (*names, name)] = enum
                    self.edges.append(Edge(node.id, enum.id, label="enum", kind="contains"))
                    for number, value in values.items():
                        label = _text(value["id"] if isinstance(value, dict) else value)
                        doc = value.get("doc", "") if isinstance(value, dict) else ""
                        enum.rows.append(Row(str(number), label, str(number), doc=doc, kind="enum"))
                pending.extend(((*names, name), child, scope) for name, child in reversed(list(spec.get("types", {}).items())))

    def resolve(self, scope: _Scope, name: str, enum: bool = False) -> Node | None:
        parts = tuple(part.strip() for part in name.split("::"))

        def find(start: _Scope, remaining: tuple[str, ...]) -> Node | None:
            if not remaining:
                return None if enum else start.node
            head, tail = remaining[0], remaining[1:]
            current: _Scope | None = start
            while current:
                # Kaitai resolves each component in its new lexical scope.
                # A qualifier's next component can therefore name a sibling
                # or an ancestor's enum, as well as a direct child.
                if enum and not tail:
                    found = self.enums.get((current.document.path, (*current.names, head)))
                else:
                    child = self.scopes.get((current.document.path, (*current.names, head)))
                    found = find(child, tail) if child else None
                if found:
                    return found
                own_name = current.names[-1] if current.names else current.document.name
                if head == own_name:
                    found = find(current, tail)
                    if found:
                        return found
                current = current.parent
            # The compiler's ClassSpecs namespace includes all loaded imports,
            # including transitive imports, rather than only direct imports.
            matches = []
            for document in self.documents.values():
                if head == document.name:
                    found = find(self.scopes[document.path, ()], tail)
                    if found:
                        matches.append(found)
            if len(matches) > 1:
                raise self.error(scope, "/", f"ambiguous imported reference {name!r}")
            return matches[0] if matches else None

        return find(scope, parts)

    def type_edge(self, scope: _Scope, row: Row, type_name: str, location: str, label: str = "") -> None:
        name = type_name.split("(", 1)[0].strip()
        if row.kind == "param":
            while name.endswith("[]"):
                name = name[:-2].strip()
            if name in _PARAM_PRIMITIVES or _PARAM_PRIMITIVE_PATTERN.fullmatch(name):
                return
        elif _PRIMITIVE.fullmatch(name):
            return
        target = self.resolve(scope, name)
        if target is None and scope.meta.get("ks-opaque-types", False):
            key = (scope.document.path, name)
            if key not in self.opaque:
                self.opaque[key] = self.add_node(title=name, subtitle="external opaque type", kind="opaque", source_file=self.source_file(scope.document.path))
            target = self.opaque[key]
        if target is None:
            raise self.error(scope, location, f"unknown type {name!r}")
        self.edges.append(Edge(scope.node.id, target.id, row.id, label=label, kind="type"))

    def add_row(self, scope: _Scope, spec: dict, label: str, kind: str, location: str) -> None:
        type_spec = spec.get("type")
        if isinstance(type_spec, dict):
            type_label = "switch (" + _text(type_spec["switch-on"]) + ")"
        elif type_spec:
            type_label = type_spec
        elif "value" in spec:
            type_label = "value"
        else:
            type_label = "bytes"
        details = []
        for key, value in spec.items():
            if key not in {"id", "type", "doc", "doc-ref", "pos", "size"} and not key.startswith("-"):
                details.append(f"{key}: {_text(value)}")
        if isinstance(type_spec, dict):
            details += [f"case {case}: {value}" for case, value in type_spec["cases"].items()]
        if "doc-ref" in spec:
            details.append("doc-ref: " + _text(spec["doc-ref"]))
        row = Row(location, label, type_label, " · ".join(details), spec.get("doc", ""), kind)
        scope.node.rows.append(row)
        if isinstance(type_spec, str):
            self.type_edge(scope, row, type_spec, location + "/type")
        elif isinstance(type_spec, dict):
            for case, value in type_spec["cases"].items():
                self.type_edge(scope, row, value, location + "/type/cases/" + case, label=case)
        if "enum" in spec:
            target = self.resolve(scope, spec["enum"], enum=True)
            if target is None:
                raise self.error(scope, location + "/enum", f"unknown enum {spec['enum']!r}")
            self.edges.append(Edge(scope.node.id, target.id, row.id, label="enum", kind="enum"))

    def populate(self) -> None:
        for scope in self.scopes.values():
            used = set()
            rows = [(item.get("id", f"_unnamed{index}"), item, "field", f"/seq/{index}") for index, item in enumerate(scope.spec.get("seq", []))]
            rows = [(item["id"], item, "param", f"/params/{index}") for index, item in enumerate(scope.spec.get("params", []))] + rows
            rows += [(name, item, "instance", f"/instances/{name}") for name, item in scope.spec.get("instances", {}).items()]
            for name, spec, kind, location in rows:
                if name in used:
                    raise self.error(scope, location, f"duplicate field/parameter/instance identifier {name!r}")
                used.add(name)
                if spec.get("repeat") == "expr" and "repeat-expr" not in spec:
                    raise self.error(scope, location, "repeat: expr requires repeat-expr")
                if spec.get("repeat") == "until" and "repeat-until" not in spec:
                    raise self.error(scope, location, "repeat: until requires repeat-until")
                self.add_row(scope, spec, name, kind, location)
        for document in self.documents.values():
            source = self.scopes[document.path, ()].node.id
            for imported in document.imports:
                target = self.scopes[imported.path, ()].node.id
                self.edges.append(Edge(source, target, label="import", kind="import"))


def load_graph(path: Path, import_paths: Sequence[Path] = ()) -> Graph:
    """Validate *path* and every imported KSY, then create their type graph.

    Relative imports start in the importing file's directory. Imports beginning
    with ``/`` search ``import_paths``, followed by ``KSPATH``. Cyclic imports
    and recursive user types are represented without repeated loading.
    """
    builder = _GraphBuilder(import_paths)
    root = builder.load(Path(path))
    builder.index()
    builder.populate()
    annotate_sizes(builder.scopes.values(), builder.resolve)
    return Graph(root.data["meta"].get("title", root.name), builder.nodes, builder.edges)
