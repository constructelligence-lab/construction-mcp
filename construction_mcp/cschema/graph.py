"""The links between records, and walking them.

Every ref and refs field is an edge. ``trace`` walks edges in both directions from one record, so from an RFI it
finds the spec section it cites, the submittals on that section, the change event the answer raised, the change
order that priced it and the costs posted against that order. ``context`` turns the same walk into a compact
brief an AI model can read: each record's fields, with where every one came from.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from . import values
from .bundle import Bundle
from .schema import Schema, load

# fields that name a record in one line, in order of preference
TITLE_FIELDS = ("number", "activity_code", "cost_code", "name", "title", "subject", "log_date")
SUMMARY_FIELDS = ("title", "subject", "name", "description")
# entities with one record per day, which swamp a trace unless asked for
BULKY = {"daily_log"}
# records many others point at (a person, the spec book, the project). A trace reaches them but does not walk back
# out through everything else that points at them, or every trace would reach every record
HUBS = {"project", "party", "document"}


@dataclass(frozen=True)
class Edge:
    source: tuple[str, str]
    field: str
    target: tuple[str, str]


def edges(bundle: Bundle, schema: Schema | None = None) -> list[Edge]:
    schema = schema or load()
    out: list[Edge] = []
    for r in bundle.records:
        entity = r.get("type")
        if entity not in schema.entities:
            continue
        for f in schema.own[entity]:
            if not f.target or values.empty(r.get(f.name)):
                continue
            refs = r[f.name] if f.many and isinstance(r[f.name], list) else [r[f.name]]
            out += [Edge((entity, str(r["id"])), f.name, (f.target, str(ref))) for ref in refs]
    return out


def label(record: dict[str, Any]) -> str:
    entity, rid = record.get("type"), record.get("id")
    name = next((str(record[k]) for k in TITLE_FIELDS if not values.empty(record.get(k))), "")
    summary = next((str(record[k]) for k in SUMMARY_FIELDS if not values.empty(record.get(k)) and str(record[k]) != name), "")
    head = f"{entity} {rid}" if not name or name == rid else f"{entity} {rid} ({name})"
    return f"{head}: {summary}" if summary else head


def parse_ref(text: str) -> tuple[str, str]:
    """``rfi:RFI-011`` -> ("rfi", "RFI-011")."""
    entity, sep, rid = text.partition(":")
    if not sep or not rid:
        raise ValueError(f"name a record as entity:id, for example rfi:RFI-011 (got {text!r})")
    return entity, rid


@dataclass
class Step:
    key: tuple[str, str]
    depth: int
    via: str  # "" for the start, else "field ->" or "<- field"
    parent: tuple[str, str] | None


def trace(bundle: Bundle, start: tuple[str, str], depth: int = 3, include_bulky: bool = False) -> list[Step]:
    """Breadth-first walk from ``start`` along links in both directions, visiting each record once.

    Incoming links are not followed out of hub records (unless the walk starts at one), and daily logs are left
    out unless ``include_bulky`` or the walk starts at one.
    """
    index = bundle.index()
    if start not in index:
        raise KeyError(f"{start[0]} {start[1]} is not in the bundle")
    out_edges: dict[tuple[str, str], list[Edge]] = {}
    in_edges: dict[tuple[str, str], list[Edge]] = {}
    for e in edges(bundle):
        out_edges.setdefault(e.source, []).append(e)
        in_edges.setdefault(e.target, []).append(e)
    seen = {start}
    steps = [Step(start, 0, "", None)]
    queue = deque([(start, 0)])
    while queue:
        key, d = queue.popleft()
        if d >= depth:
            continue
        hops = [(e.target, f"{e.field} ->") for e in out_edges.get(key, [])]
        if key == start or key[0] not in HUBS:
            hops += [(e.source, f"<- {e.field}") for e in in_edges.get(key, [])]
        for nxt, via in hops:
            if nxt in seen or nxt not in index:
                continue
            if not include_bulky and nxt[0] in BULKY and start[0] not in BULKY:
                continue
            seen.add(nxt)
            steps.append(Step(nxt, d + 1, via, key))
            queue.append((nxt, d + 1))
    return steps


def render_tree(bundle: Bundle, steps: list[Step]) -> str:
    index = bundle.index()
    children: dict[tuple[str, str] | None, list[Step]] = {}
    for s in steps:
        children.setdefault(s.parent, []).append(s)
    lines: list[str] = []

    def walk(step: Step, indent: int) -> None:
        arrow = f"{step.via}  " if step.via else ""
        lines.append("  " * indent + arrow + label(index[step.key]))
        for child in children.get(step.key, []):
            walk(child, indent + 1)

    walk(steps[0], 0)
    return "\n".join(lines)


def _fields_line(record: dict[str, Any], schema: Schema) -> list[str]:
    entity = record["type"]
    out = []
    for f in schema.own.get(entity, ()):
        v = record.get(f.name)
        if values.empty(v) or f.target:
            continue
        out.append(f"{f.name}: {v}")
    for c in record.get("classifications") or []:
        if isinstance(c, dict):
            out.append(f"{c.get('system')}: {c.get('code')}" + (f" ({c['title']})" if c.get("title") else ""))
    return out


def _provenance(record: dict[str, Any]) -> str:
    src = record.get("source") or {}
    bits = [str(src.get("system", "unknown"))]
    if src.get("record_id"):
        bits.append(f"record {src['record_id']}")
    if src.get("method"):
        bits.append(f"via {src['method']}")
    if src.get("confidence") is not None:
        bits.append(f"confidence {src['confidence']}")
    if src.get("retrieved_at"):
        bits.append(f"read {src['retrieved_at']}")
    return ", ".join(bits)


def context(bundle: Bundle, steps: list[Step], schema: Schema | None = None) -> str:
    """A Markdown brief of the walked records for a model's context window, provenance on every record."""
    schema = schema or load()
    index = bundle.index()
    start = index[steps[0].key]
    lines = [f"# {label(start)}", "", f"{len(steps)} linked records. Every record states where it came from;"
             " values read by document or model carry a confidence below 1.", ""]
    for s in steps:
        r = index[s.key]
        if not s.parent:
            how = "the record asked about"
        elif s.via.endswith("->"):
            how = f"{s.parent[0]} {s.parent[1]} points at it ({s.via[:-3]})"
        else:
            how = f"it points at {s.parent[0]} {s.parent[1]} ({s.via[3:]})"
        lines.append(f"## {label(r)}")
        lines.append(f"- link: {how}")
        lines.append(f"- source: {_provenance(r)}")
        lines += [f"- {x}" for x in _fields_line(r, schema)]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def as_json(bundle: Bundle, steps: list[Step]) -> dict[str, Any]:
    index = bundle.index()
    return {
        "start": {"type": steps[0].key[0], "id": steps[0].key[1]},
        "records": [
            {"depth": s.depth, "via": s.via, "parent": None if not s.parent else {"type": s.parent[0], "id": s.parent[1]},
             "record": index[s.key]}
            for s in steps
        ],
    }
