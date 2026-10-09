"""Checks a bundle against the schema.

Every check has a code, listed in CHECKS (and in the table in SCHEMA.md; a test keeps them in step). Errors mean a
record cannot be trusted or placed; warnings mean it can, but something about it needs a look.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import values
from .bundle import Bundle, Finding
from .schema import Schema, load

CHECKS = {
    "B01": ("error", "A line is not a JSON object"),
    "B02": ("error", "The record's type is not an entity in the schema"),
    "B03": ("error", "The record has no id"),
    "B04": ("error", "Two records of one entity share an id"),
    "B05": ("error", "A required field is missing or empty"),
    "B06": ("error", "A value is not of the field's type (date, money, number, list of ids...)"),
    "B07": ("error", "A value is not one of the field's allowed values"),
    "B08": ("warning", "A field is not in the schema; move it under extra"),
    "B09": ("error", "A reference points at a record that is not in the bundle"),
    "B10": ("error", "A reference points at a record on a different project"),
    "B11": ("error", "Provenance is missing or incomplete, or a document or model value has no confidence"),
    "B12": ("warning", "A classification is malformed: unknown system, or a code that does not fit it"),
    "B13": ("warning", "Dates are out of order (answered before asked, finished before started)"),
    "B14": ("error", "A commitment change order does not name its commitment"),
    "B15": ("warning", "A percentage or confidence is out of range"),
}

# (entity, earlier field, later field)
DATE_ORDER = [
    ("project", "start_date", "finish_date"),
    ("rfi", "created_date", "answered_date"),
    ("submittal", "submitted_date", "returned_date"),
    ("change_event", "event_date", "notice_date"),
    ("schedule_activity", "planned_start", "planned_finish"),
    ("schedule_activity", "actual_start", "actual_finish"),
    ("punch_item", "created_date", "closed_date"),
    ("asset", "warranty_start_date", "warranty_end_date"),
]
PERCENTS = {("project", "retainage_pct"), ("commitment", "retainage_pct"), ("schedule_activity", "percent_complete")}
CODE_FORMATS = {
    "masterformat": re.compile(r"^\d{2}( \d{2}){1,2}(\.\d{2})?$"),
    "csi_division": re.compile(r"^\d{2}$"),
    "uniformat": re.compile(r"^[A-G](\d{2}){0,2}(\d{2})?$"),
    "omniclass": re.compile(r"^\d{2}-\d{2}( \d{2}){0,4}$"),
    "uniclass": re.compile(r"^[A-Z][a-z]?(_\d{2}){1,4}$"),
}
STRUCTURAL = {"type", "source", "classifications", "extra"}


def _f(code: str, message: str, where: str) -> Finding:
    return Finding(code, CHECKS[code][0], message, where)


def _source(rec: dict[str, Any], schema: Schema, ids: set[tuple[str, str]], where: str) -> list[Finding]:
    src = rec.get("source")
    label = f"{rec.get('type')} {rec.get('id')}"
    if not isinstance(src, dict):
        return [_f("B11", f"{label} has no source", where)]
    out: list[Finding] = []
    for f in schema.parts["source"]:
        v = src.get(f.name)
        if values.empty(v):
            if f.required:
                out.append(_f("B11", f"{label} source has no {f.name}", where))
            continue
        try:
            parsed = values.PARSERS[f.base](v)
        except ValueError as exc:
            out.append(_f("B06", f"{label} source.{f.name}: {exc}", where))
            continue
        if f.allowed and parsed not in f.allowed:
            out.append(_f("B07", f"{label} source.{f.name} {v!r} is not one of {'|'.join(f.allowed)}", where))
        if f.name == "confidence" and not 0 <= parsed <= 1:
            out.append(_f("B15", f"{label} source.confidence {v} is outside 0 to 1", where))
        if f.target and ("document", v) not in ids:
            out.append(_f("B09", f"{label} source.document_id {v!r} is not in the bundle", where))
    for k in src:
        if k not in {f.name for f in schema.parts["source"]}:
            out.append(_f("B08", f"{label} source.{k} is not a source field", where))
    if src.get("method") in ("document", "model") and values.empty(src.get("confidence")):
        out.append(_f("B11", f"{label} was read by {src['method']} but has no source.confidence", where))
    return out


def _classifications(rec: dict[str, Any], schema: Schema, where: str) -> list[Finding]:
    items = rec.get("classifications")
    if items is None:
        return []
    label = f"{rec.get('type')} {rec.get('id')}"
    if not isinstance(items, list):
        return [_f("B06", f"{label} classifications is not a list", where)]
    systems = next(f for f in schema.parts["classification"] if f.name == "system").allowed
    out: list[Finding] = []
    for c in items:
        if not isinstance(c, dict) or values.empty(c.get("system")) or values.empty(c.get("code")):
            out.append(_f("B12", f"{label} has a classification without system and code", where))
            continue
        system, code = c["system"], str(c["code"])
        if system not in systems:
            out.append(_f("B12", f"{label} classification system {system!r} is not one of {'|'.join(systems)}", where))
        elif system in CODE_FORMATS and not CODE_FORMATS[system].match(code):
            out.append(_f("B12", f"{label} {system} code {code!r} does not look like a {system} code", where))
        conf = c.get("confidence")
        if conf is not None:
            try:
                if not 0 <= values.number(conf) <= 1:
                    out.append(_f("B15", f"{label} classification confidence {conf} is outside 0 to 1", where))
            except ValueError as exc:
                out.append(_f("B06", f"{label} classification confidence: {exc}", where))
    return out


def validate(bundle: Bundle, schema: Schema | None = None) -> list[Finding]:
    schema = schema or load()
    findings: list[Finding] = list(bundle.problems)
    ids = {(str(r.get("type")), str(r.get("id"))) for r in bundle.records if not values.empty(r.get("id"))}
    projects = {(str(r.get("type")), str(r.get("id"))): r.get("project_id") for r in bundle.records}
    counts = Counter((r.get("type"), r.get("id")) for r in bundle.records if not values.empty(r.get("id")))
    reported: set[tuple[Any, Any]] = set()

    for rec, where in zip(bundle.records, bundle.where):
        entity = rec.get("type")
        if entity not in schema.entities:
            findings.append(_f("B02", f"type {entity!r} is not an entity in the schema", where))
            continue
        rid = rec.get("id")
        if values.empty(rid):
            findings.append(_f("B03", f"{entity} record has no id", where))
            continue
        label = f"{entity} {rid}"
        if counts[(entity, rid)] > 1 and (entity, rid) not in reported:
            reported.add((entity, rid))
            findings.append(_f("B04", f"{counts[(entity, rid)]} {entity} records share id {rid!r}", where))

        fields = {f.name: f for f in schema.fields(entity)}
        parsed: dict[str, Any] = {}
        for name, f in fields.items():
            if f.base in ("source", "classifications", "object"):
                continue
            v = rec.get(name)
            if values.empty(v):
                if f.required:
                    findings.append(_f("B05", f"{label} has no {name}", where))
                continue
            try:
                parsed[name] = values.PARSERS[f.base](v)
            except ValueError as exc:
                findings.append(_f("B06", f"{label} {name}: {exc}", where))
                continue
            if f.allowed and v not in f.allowed:
                findings.append(_f("B07", f"{label} {name} {v!r} is not one of {'|'.join(f.allowed)}", where))
            target = f.target
            if target:
                for ref in parsed[name] if f.many else [parsed[name]]:
                    if (target, ref) not in ids:
                        findings.append(_f("B09", f"{label} {name} points at {target} {ref!r}, not in the bundle", where))
                        continue
                    theirs = projects.get((target, ref))
                    if schema.entities[target].project_scoped and theirs and rec.get("project_id") and theirs != rec["project_id"]:
                        findings.append(_f("B10", f"{label} ({rec['project_id']}) {name} points at {target} {ref} on {theirs}", where))
            if (entity, name) in PERCENTS and not 0 <= parsed[name] <= 100:
                findings.append(_f("B15", f"{label} {name} {v} is outside 0 to 100", where))

        for k in rec:
            if k not in fields and k not in STRUCTURAL:
                findings.append(_f("B08", f"{label} field {k!r} is not in the schema for {entity}; move it under extra", where))
        if "extra" in rec and not isinstance(rec["extra"], dict):
            findings.append(_f("B06", f"{label} extra must be a JSON object", where))

        findings += _source(rec, schema, ids, where)
        findings += _classifications(rec, schema, where)

        for ent, early, late in DATE_ORDER:
            if ent == entity and early in parsed and late in parsed and parsed[late] < parsed[early]:
                findings.append(_f("B13", f"{label} {late} {rec[late]} is before {early} {rec[early]}", where))
        if entity == "change_order" and rec.get("level") == "commitment" and values.empty(rec.get("commitment_id")):
            findings.append(_f("B14", f"{label} is a commitment change order with no commitment_id", where))
    return findings


def summary(findings: list[Finding]) -> str:
    e = sum(f.severity == "error" for f in findings)
    w = sum(f.severity == "warning" for f in findings)
    return f"{e} error{'s' * (e != 1)}, {w} warning{'s' * (w != 1)}"
