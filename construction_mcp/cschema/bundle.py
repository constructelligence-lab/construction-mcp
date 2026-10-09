"""Bundles: a project's records as JSON Lines, one record per line.

A record is a flat JSON object: ``type`` names the entity, ``id`` is unique within that entity, and the remaining
keys are the entity's fields (see SCHEMA.md). A bundle is one ``.jsonl`` file or a folder of them.

    {"type": "rfi", "id": "RFI-011", "project_id": "P-2041", "number": "RFI-011", ...,
     "source": {"system": "procore", "record_id": "88213", "method": "api"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Finding:
    check: str
    severity: str  # error | warning
    message: str
    where: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "severity": self.severity, "message": self.message, "where": self.where}


@dataclass
class Bundle:
    records: list[dict[str, Any]] = field(default_factory=list)
    where: list[str] = field(default_factory=list)  # file:line per record
    problems: list[Finding] = field(default_factory=list)  # lines that could not be read

    def add(self, record: dict[str, Any], where: str = "") -> None:
        self.records.append(record)
        self.where.append(where or f"record {len(self.records)}")

    def index(self) -> dict[tuple[str, str], dict[str, Any]]:
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in self.records:
            key = (str(r.get("type")), str(r.get("id")))
            out.setdefault(key, r)
        return out

    def of(self, entity: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("type") == entity]


def from_records(records: Iterable[dict[str, Any]]) -> Bundle:
    b = Bundle()
    for i, r in enumerate(records, 1):
        if isinstance(r, dict):
            b.add(r, f"record {i}")
        else:
            b.problems.append(Finding("B01", "error", f"record {i} is not a JSON object", f"record {i}"))
    return b


def load(path: Path) -> Bundle:
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"no .jsonl files in {path}")
    b = Bundle()
    for f in files:
        with f.open(encoding="utf-8") as handle:
            for n, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                where = f"{f.name}:{n}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    b.problems.append(Finding("B01", "error", f"not valid JSON: {exc.msg}", where))
                    continue
                if not isinstance(record, dict):
                    b.problems.append(Finding("B01", "error", "line is not a JSON object", where))
                    continue
                b.add(record, where)
    return b


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot write {type(value).__name__}")


ORDER = ("type", "id", "project_id")


def dumps(record: dict[str, Any]) -> str:
    """One record as a JSON line with type, id and project_id first and empty values left out."""
    ordered = {k: record[k] for k in ORDER if k in record}
    ordered.update({k: v for k, v in record.items() if k not in ORDER and v not in (None, "", [], {})})
    return json.dumps(ordered, ensure_ascii=False, default=_plain)


def write(records: Iterable[dict[str, Any]], path: Path) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for r in records:
            handle.write(dumps(r) + "\n")
            n += 1
    return n
