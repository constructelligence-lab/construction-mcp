"""Tools for project records in the construction schema: describe it, validate a bundle, trace the links.

The schema and the code that reads it are copied from construction-schema (see ``cschema/SOURCE.md``). Records come
in as tool arguments; nothing is read from disk but the schema, and nothing is kept between calls.
"""

from __future__ import annotations

from typing import Any

from .cschema import bundle, graph
from .cschema.schema import SCHEMA_DIR, VERSION, load
from .cschema.validate import summary, validate
from .protocol import ToolError

SOURCE = "construction-schema (CC BY 4.0, Constructelligence): https://github.com/constructelligence-lab/construction-schema"
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
MAX_RECORDS = 5000
MAX_FINDINGS = 200


def _records(args: dict[str, Any]) -> bundle.Bundle:
    records = args.get("records")
    if not isinstance(records, list) or not records:
        raise ToolError("records must be a non-empty list of record objects, one per record, each with type and id")
    if len(records) > MAX_RECORDS:
        raise ToolError(f"at most {MAX_RECORDS} records per call; split the bundle by project")
    return bundle.from_records(records)


def schema_describe(args: dict[str, Any]) -> dict[str, Any]:
    schema = load()
    entity = args.get("entity")
    if not entity:
        return {
            "version": VERSION,
            "entities": [{"entity": e.name, "label": e.label, "group": e.group, "description": e.description} for e in schema.entities.values()],
            "record_shape": "One JSON object per record: type (the entity), id, project_id (except project and party), the entity's fields, "
                            "source {system, record_id, method, retrieved_at, confidence, document_id}, optional classifications "
                            "[{system, code, title, confidence}] and extra {}.",
            "source": SOURCE,
        }
    if entity not in schema.entities:
        raise ToolError(f"unknown entity {entity!r}; one of: {', '.join(schema.entities)}")
    e = schema.entities[entity]

    def field(f) -> dict[str, Any]:
        out: dict[str, Any] = {"field": f.name, "type": f.type, "required": f.required, "meaning": f.description}
        if f.allowed:
            out["allowed"] = list(f.allowed)
        return out

    return {
        "entity": e.name,
        "label": e.label,
        "description": e.description,
        "fields": [field(f) for f in schema.fields(entity)],
        "source_fields": [field(f) for f in schema.parts["source"]],
        "pointed_at_by": [f"{link.entity}.{link.field}" for link in schema.incoming(entity)],
        "source": SOURCE,
    }


def records_validate(args: dict[str, Any]) -> dict[str, Any]:
    b = _records(args)
    findings = validate(b)
    counts: dict[str, int] = {}
    for r in b.records:
        counts[str(r.get("type"))] = counts.get(str(r.get("type")), 0) + 1
    return {
        "records": len(b.records),
        "by_entity": counts,
        "summary": summary(findings),
        "valid": not any(f.severity == "error" for f in findings),
        "findings": [f.as_dict() for f in findings[:MAX_FINDINGS]],
        **({"findings_truncated": len(findings) - MAX_FINDINGS} if len(findings) > MAX_FINDINGS else {}),
    }


def records_trace(args: dict[str, Any]) -> dict[str, Any]:
    b = _records(args)
    start = args.get("start")
    if not isinstance(start, str):
        raise ToolError("start names the record to begin from as entity:id, for example rfi:RFI-011")
    depth = args.get("depth", 3)
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 6:
        raise ToolError("depth must be a whole number from 1 to 6")
    try:
        steps = graph.trace(b, graph.parse_ref(start), depth=depth, include_bulky=bool(args.get("include_daily_logs")))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"") + "; check the type and id") from None
    out = graph.as_json(b, steps)
    out["tree"] = graph.render_tree(b, steps)
    out["brief"] = graph.context(b, steps)
    out["note"] = ("Links are only as complete as the records given. A value whose source method is document or model "
                   "was read by software and carries a confidence; say so when relying on it.")
    return out


RECORDS_PARAM = {
    "type": "array",
    "items": {"type": "object"},
    "description": "The records, one object each: {\"type\": \"rfi\", \"id\": \"RFI-011\", \"project_id\": \"P-2041\", ..., \"source\": {\"system\": \"procore\", \"method\": \"api\"}}. Call schema_describe for the fields.",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "schema_describe",
        "title": "Describe the construction schema",
        "description": "The construction schema's entities (project, RFI, submittal, change event, change order, commitment, budget line, cost line, billing, schedule activity, daily log, observation, punch item, asset and more), or one entity's fields, allowed values and the links that point at it. Use it before building records for records_validate or records_trace.",
        "inputSchema": {
            "type": "object",
            "properties": {"entity": {"type": "string", "description": "An entity name, e.g. 'change_order'. Leave out to list them all"}},
            "required": [],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "records_validate",
        "title": "Validate project records",
        "description": "Check construction project records against the schema: required fields, types, allowed values, duplicate ids, broken or cross-project links, provenance (a value read from a document or by a model must carry a confidence), classification codes and date order. Returns findings with check codes.",
        "inputSchema": {
            "type": "object",
            "properties": {"records": RECORDS_PARAM},
            "required": ["records"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "records_trace",
        "title": "Trace linked project records",
        "description": "Follow the links from one record in both directions: from an RFI to the spec sections it cites, the submittals and assets on them, the change events the answer raised, the change orders that priced them and the costs and pay applications behind those. Returns the linked records, a tree, and a Markdown brief with the source of every record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "records": RECORDS_PARAM,
                "start": {"type": "string", "description": "The record to start from, as entity:id, e.g. 'rfi:RFI-011'"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3, "description": "How many links to follow"},
                "include_daily_logs": {"type": "boolean", "default": False, "description": "Daily logs are left out unless asked for, since there is one per day"},
            },
            "required": ["records", "start"],
            "additionalProperties": False,
        },
        "annotations": READ_ONLY,
    },
]

HANDLERS = {
    "schema_describe": schema_describe,
    "records_validate": records_validate,
    "records_trace": records_trace,
}

RESOURCES: list[dict[str, Any]] = [
    {
        "uri": f"construction-reference://schema/{name}",
        "name": f"schema-{name}",
        "title": title,
        "description": f"{title}. CSV, CC BY 4.0, from construction-schema.",
        "mimeType": "text/csv",
        "read": (lambda n=name: (SCHEMA_DIR / n).read_text(encoding="utf-8")),
    }
    for name, title in {
        "entities.csv": "Construction schema entities",
        "fields.csv": "Construction schema fields, types, allowed values and links",
    }.items()
]
