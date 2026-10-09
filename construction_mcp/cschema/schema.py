"""The schema: entities, their fields, and the links between them, read from ``schema/*.csv``.

The CSV files are the source of truth so a project engineer can read the schema in a spreadsheet. The JSON Schema
files and SCHEMA.md are generated from them (``python3 -m cschema schema``).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# a copy of the package vendored elsewhere (construction-mcp) carries the CSVs inside it
_BUNDLED = Path(__file__).resolve().parent / "schema"
SCHEMA_DIR = _BUNDLED if _BUNDLED.is_dir() else ROOT / "schema"
VERSION = "0.1.0"

# pseudo-entities in fields.csv that describe shared parts rather than record types
COMMON = "*"
PARTS = ("source", "classification")


@dataclass(frozen=True)
class Field:
    entity: str
    name: str
    type: str
    required: bool
    allowed: tuple[str, ...]
    description: str

    @property
    def target(self) -> str | None:
        """The entity a ref or refs field points at."""
        return self.type.split(":", 1)[1] if self.type.startswith(("ref:", "refs:")) else None

    @property
    def many(self) -> bool:
        return self.type.startswith("refs:")

    @property
    def base(self) -> str:
        return self.type.split(":", 1)[0]


@dataclass(frozen=True)
class Entity:
    name: str
    label: str
    project_scoped: bool
    group: str
    description: str


@dataclass(frozen=True)
class Link:
    entity: str
    field: str
    target: str
    many: bool
    description: str


@dataclass(frozen=True)
class Schema:
    entities: dict[str, Entity]
    own: dict[str, tuple[Field, ...]]  # entity -> its own fields
    common: tuple[Field, ...]
    parts: dict[str, tuple[Field, ...]]  # source / classification

    def fields(self, entity: str) -> tuple[Field, ...]:
        """Every field a record of ``entity`` may carry: the common ones it uses, then its own."""
        scoped = self.entities[entity].project_scoped
        common = tuple(f for f in self.common if f.name != "project_id" or scoped)
        return common + self.own[entity]

    def field(self, entity: str, name: str) -> Field | None:
        return next((f for f in self.fields(entity) if f.name == name), None)

    def links(self) -> list[Link]:
        out = [Link(f.entity, f.name, f.target, f.many, f.description) for fs in self.own.values() for f in fs if f.target]
        return out

    def incoming(self, entity: str) -> list[Link]:
        return [link for link in self.links() if link.target == entity]


def _read(name: str) -> list[dict[str, str]]:
    with (SCHEMA_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=None)
def load() -> Schema:
    entities = {
        r["entity"]: Entity(r["entity"], r["label"], r["project_scoped"] == "yes", r["group"], r["description"])
        for r in _read("entities.csv")
    }
    own: dict[str, list[Field]] = {name: [] for name in entities}
    common: list[Field] = []
    parts: dict[str, list[Field]] = {p: [] for p in PARTS}
    for r in _read("fields.csv"):
        f = Field(
            r["entity"], r["field"], r["type"], r["required"] == "yes",
            tuple(a for a in r["allowed"].split("|") if a), r["description"],
        )
        if f.entity == COMMON:
            common.append(f)
        elif f.entity in parts:
            parts[f.entity].append(f)
        elif f.entity in own:
            own[f.entity].append(f)
        else:
            raise ValueError(f"fields.csv: unknown entity {f.entity!r}")
    return Schema(
        entities,
        {k: tuple(v) for k, v in own.items()},
        tuple(common),
        {k: tuple(v) for k, v in parts.items()},
    )
