"""Conservative encoded sizes and local offsets, without evaluating expressions.

All arithmetic uses bit offsets internally. Conditional ranges are bounds, not a
claim that every intermediate size is possible. Custom types share the caller's
bit cursor; explicit ``size`` fields create a byte-sized substream instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re
from typing import Any

from .model import Node


_BYTE_TYPE = re.compile(r"[usf]([1248])(?:le|be)?\Z")
_BIT_TYPE = re.compile(r"b([0-9]+)(?:le|be)?\Z")
_INTEGER = re.compile(r"\+?(?:[0-9][0-9_]*|0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+)\Z")
_CONDITION = re.compile(r"(?:(not\s+|!)\s*)?([A-Za-z_][A-Za-z0-9_]*)\Z")


@dataclass(frozen=True)
class _Range:
    low: int = 0
    high: int | None = None

    @classmethod
    def exact(cls, value: int) -> _Range:
        return cls(value, value)

    def add(self, other: _Range) -> _Range:
        high = None if self.high is None or other.high is None else self.high + other.high
        return _Range(self.low + other.low, high)

    def times(self, count: int) -> _Range:
        if not count:
            return _Range.exact(0)
        return _Range(self.low * count, None if self.high is None else self.high * count)

    def shifted(self, offset: int) -> _Range:
        return _Range(self.low + offset, None if self.high is None else self.high + offset)

    def aligned(self) -> _Range:
        return _Range((self.low + 7) // 8 * 8, None if self.high is None else (self.high + 7) // 8 * 8)

    def union(self, other: _Range) -> _Range:
        high = None if self.high is None or other.high is None else max(self.high, other.high)
        return _Range(min(self.low, other.low), high)

    def distance(self, start: _Range) -> _Range:
        # With uncertain cursors the endpoints may be correlated. Widen the
        # bounds rather than pretending their difference is an exact size.
        low = 0 if start.high is None else max(0, self.low - start.high)
        high = None if self.high is None else max(0, self.high - start.low)
        return _Range(low, high)


@dataclass(frozen=True)
class _Read:
    pos: _Range
    end: _Range
    size: _Range
    bits: bool = False
    label: str = ""


def _integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        value = value.strip().lstrip("+").replace("_", "")
        try:
            return int(value, 0 if value.lower().startswith(("0x", "0b", "0o")) else 10)
        except ValueError:
            pass
    return None


def _truth(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip() in ("true", "false"):
        return value.strip() == "true"
    return None


def _position(value: _Range) -> str:
    def point(bits: int) -> str:
        byte, bit = divmod(bits, 8)
        return f"{byte}.{bit}" if bit else str(byte)

    if value.high is None:
        return "?"
    if value.low == value.high:
        return point(value.low)
    return f"{point(value.low)}–{point(value.high)}"


def _size(value: _Range, bits: bool = False) -> str:
    if value.high is None:
        return "?"
    bits = bits or value.low % 8 != 0 or value.high % 8 != 0
    divisor, unit = (1, "bit") if bits else (8, "B")
    low, high = value.low // divisor, value.high // divisor
    return f"{low} {unit}" if low == high else f"{low}–{high} {unit}"


def _contents_size(value: Any) -> int | None:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, str):
                total += len(item.encode("utf-8"))
            elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255:
                total += 1
            else:
                return None
        return total
    return None


class _Analyzer:
    def __init__(self, scopes: Iterable[Any], resolve: Callable[..., Node | None]):
        self.scopes = {scope.node.id: scope for scope in scopes}
        self.resolve = resolve
        self.span_cache: dict[tuple[str, int], _Range] = {}
        self.sequence_cache: dict[tuple[str, int], tuple[_Range, _Range]] = {}

    def sequence(self, scope: Any, cursor: _Range, active: frozenset[str]) -> tuple[_Range, _Range]:
        if scope.node.id in active or len(active) >= 128:
            return cursor, _Range(cursor.low, None)
        if cursor.low != cursor.high:
            # Reads and alignment are monotone in the input cursor. Evaluate
            # only the bounding exact positions, keeping recursive DAGs small.
            first, end = self.sequence(scope, _Range.exact(cursor.low), active)
            if cursor.high is None:
                return _Range(first.low, None), _Range(end.low, None)
            last_first, last_end = self.sequence(scope, _Range.exact(cursor.high), active)
            return first.union(last_first), end.union(last_end)
        residue = cursor.low % 8
        base = cursor.low - residue
        key = (scope.node.id, residue)
        if key in self.sequence_cache:
            first, end = self.sequence_cache[key]
            return first.shifted(base), end.shifted(base)
        active = active | {scope.node.id}
        cursor = _Range.exact(residue)
        reads, end = self.sequence_reads(scope, cursor, active)
        first = reads[0].pos if reads else cursor
        self.sequence_cache[key] = (first, end)
        return first.shifted(base), end.shifted(base)

    def sequence_reads(self, scope: Any, cursor: _Range, active: frozenset[str]) -> tuple[list[_Read], _Range]:
        # Preserve simple boolean condition correlation: `if: enabled` and
        # `if: not enabled` describe mutually exclusive paths. This is purely
        # structural matching, never expression evaluation. Bound the number
        # of paths and fall back to conservative intervals for larger inputs.
        states: list[tuple[_Range, dict[str, bool]]] = [(cursor, {})]
        reads = []
        for spec in scope.spec.get("seq", []):
            alternatives = []
            updated = []
            condition = spec.get("if")
            match = _CONDITION.fullmatch(condition.strip()) if isinstance(condition, str) and _truth(condition) is None else None
            for position, known in states:
                if match and (match[2] in known or len(states) < 32):
                    name, invert = match[2], bool(match[1])
                    choices = [known[name]] if name in known else [False, True]
                    for value in choices:
                        read = self.field(scope, {**spec, "if": value != invert}, position, active)
                        updated.append((read.end, {**known, name: value}))
                        alternatives.append(read)
                else:
                    read = self.field(scope, spec, position, active)
                    updated.append((read.end, known))
                    alternatives.append(read)
            result = alternatives[0]
            for other in alternatives[1:]:
                result = _Read(result.pos.union(other.pos), result.end.union(other.end), result.size.union(other.size), result.bits or other.bits, result.label if result.label == other.label else "")
            reads.append(result)
            states = updated
        end = states[0][0]
        for position, _ in states[1:]:
            end = end.union(position)
        return reads, end

    def type_span(self, scope: Any, cursor: _Range, active: frozenset[str]) -> _Range:
        if scope.node.id in active:
            return _Range()
        residues = range(8) if cursor.high is None or cursor.high - cursor.low >= 7 else {value % 8 for value in range(cursor.low, cursor.high + 1)}
        result = None
        for residue in residues:
            key = (scope.node.id, residue)
            if key not in self.span_cache:
                start, end = self.sequence(scope, _Range.exact(residue), active)
                self.span_cache[key] = end.distance(start)
            size = self.span_cache[key]
            result = size if result is None else result.union(size)
        return result or _Range.exact(0)

    def atom(self, scope: Any, spec: dict, cursor: _Range, active: frozenset[str]) -> _Read:
        type_spec = spec.get("type")
        # The outer read consumes the entire size-limited substream even when
        # its nested parser consumes less, or the switch has no matching case.
        if "size" in spec:
            count = _integer(spec["size"])
            size = _Range.exact(count * 8) if count is not None else _Range()
            pos = cursor.aligned()
            label = "" if count is not None else f"{spec['size']} B"
            return _Read(pos, pos.add(size), size, label=label)
        if spec.get("size-eos"):
            pos = cursor.aligned()
            return _Read(pos, _Range(pos.low, None), _Range(), label="to EOS")
        if "contents" in spec:
            count = _contents_size(spec["contents"])
            size = _Range.exact(count * 8) if count is not None else _Range()
            pos = cursor.aligned()
            return _Read(pos, pos.add(size), size)
        if isinstance(type_spec, dict):
            alternatives = [
                self.atom(scope, {**spec, "type": case}, cursor, active)
                for case in type_spec["cases"].values()
            ]
            keys = set(type_spec["cases"])
            # A switch without a default can omit its read. The boolean pair
            # is the one exhaustive expression domain we can prove locally.
            if "_" not in keys and not {"true", "false"}.issubset(keys):
                alternatives.append(_Read(cursor, cursor, _Range.exact(0)))
            if not alternatives:
                return _Read(cursor, cursor, _Range.exact(0))
            result = alternatives[0]
            for other in alternatives[1:]:
                result = _Read(result.pos.union(other.pos), result.end.union(other.end), result.size.union(other.size), result.bits or other.bits)
            return result
        if isinstance(type_spec, str):
            match = _BIT_TYPE.fullmatch(type_spec)
            if match:
                width = _integer(match[1])
                size = _Range.exact(width) if width is not None else _Range()
                return _Read(cursor, cursor.add(size), size, bits=True)
            match = _BYTE_TYPE.fullmatch(type_spec)
            if match:
                size = _Range.exact(int(match[1]) * 8)
                pos = cursor.aligned()
                return _Read(pos, pos.add(size), size)
            if type_spec not in ("str", "strz"):
                target = self.resolve(scope, type_spec.split("(", 1)[0].strip())
                child = self.scopes.get(target.id) if target else None
                if child:
                    pos, end = self.sequence(child, cursor, active)
                    return _Read(pos, end, self.type_span(child, cursor, active))
        pos = cursor.aligned()
        return _Read(pos, _Range(pos.low, None), _Range())

    def field(self, scope: Any, spec: dict, cursor: _Range, active: frozenset[str]) -> _Read:
        if "value" in spec:
            return _Read(cursor, cursor, _Range.exact(0))
        result = self.atom(scope, spec, cursor, active)
        repeat = spec.get("repeat")
        if repeat == "expr":
            count = _integer(spec.get("repeat-expr"))
            simple = not isinstance(spec.get("type"), dict) and (
                "size" in spec or "contents" in spec
                or bool(_BYTE_TYPE.fullmatch(str(spec.get("type", ""))))
                or bool(_BIT_TYPE.fullmatch(str(spec.get("type", ""))))
            )
            if count is None:
                unit = result.label or _size(result.size, result.bits) if simple else "?"
                label = f"{spec.get('repeat-expr', '?')} × {unit}"
                result = _Read(result.pos, _Range(cursor.low, None), _Range(), result.bits, label)
            elif count == 0:
                result = _Read(cursor, cursor, _Range.exact(0), result.bits)
            elif count > 1:
                # A direct bit read packs continuously; byte reads have an
                # integral width. A custom bit/byte mixture can align again
                # on each iteration, so do not just multiply its first span.
                if simple:
                    size = result.size.times(count)
                    result = _Read(result.pos, result.pos.add(size), size, result.bits)
                elif count <= 64:
                    end = result.end
                    for _ in range(count - 1):
                        end = self.atom(scope, spec, end, active).end
                    result = _Read(result.pos, end, end.distance(result.pos), result.bits)
                else:
                    # Large custom repeats are bounded instead of executing
                    # a loop controlled by untrusted input.
                    result = _Read(result.pos, _Range(cursor.low, None), _Range(), label=f"{count} × ?")
        elif repeat:
            result = _Read(result.pos, _Range(cursor.low, None), _Range(), result.bits, "to EOS" if repeat == "eos" else "variable")
        condition = _truth(spec["if"]) if "if" in spec else True
        if condition is False:
            return _Read(cursor, cursor, _Range.exact(0), result.bits)
        if condition is None:
            label = f"0 / {result.label}" if result.label else ""
            return _Read(result.pos, cursor.union(result.end), _Range.exact(0).union(result.size), result.bits, label)
        return result

    def annotate(self) -> None:
        for scope in self.scopes.values():
            active = frozenset({scope.node.id})
            rows = {row.id: row for row in scope.node.rows}
            reads, cursor = self.sequence_reads(scope, _Range.exact(0), active)
            for index, read in enumerate(reads):
                row = rows[f"/seq/{index}"]
                row.pos_label = _position(read.pos)
                row.size_label = read.label or _size(read.size, read.bits)
            scope.node.size_label = _size(cursor)
            for name, spec in scope.spec.get("instances", {}).items():
                if "value" in spec:
                    continue
                row = rows[f"/instances/{name}"]
                position = _integer(spec.get("pos"))
                start = _Range.exact(position * 8) if position is not None else _Range()
                row.pos_label = str(spec["pos"]) if "pos" in spec else "?"
                read = self.field(scope, spec, start, active)
                row.size_label = read.label or _size(read.size, read.bits)


def annotate_sizes(scopes: Iterable[Any], resolve: Callable[..., Node | None]) -> None:
    """Attach display metadata; lazy instances never advance sequence offsets."""
    _Analyzer(scopes, resolve).annotate()
