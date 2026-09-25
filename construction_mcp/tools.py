"""The tools: lookups over bundled reference data, and a few plain calculators.

All data comes from the files in ``data/``, copied from the CC BY 4.0 construction-data repository
(see ``data/SOURCE.md``). Nothing is fetched, written or remembered between calls.
"""

from __future__ import annotations

import csv
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from .protocol import ToolError

DATA = Path(__file__).resolve().parent / "data"
SOURCE = "construction-data (CC BY 4.0, Constructelligence): https://github.com/constructelligence-lab/construction-data"
PLANNING_NOTE = "Planning values for early estimates. Replace with your own measured history and supplier data."

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# unit conversion categories that are simple multiplications; the others are material factors or not linear
LINEAR_CATEGORIES = {"Length", "Area", "Volume", "Weight", "Mass per area", "Pressure", "Density", "Energy", "Power"}


# --------------------------------------------------------------------------- helpers

@lru_cache(maxsize=None)
def _rows(name: str) -> tuple[dict[str, str], ...]:
    with (DATA / name).open(newline="", encoding="utf-8") as handle:
        return tuple(csv.DictReader(handle))


def _text(args: dict[str, Any], key: str, required: bool = True) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError(f"'{key}' is required")
        return ""
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a string")
    return value.strip()


def _number(args: dict[str, Any], key: str, required: bool = True, minimum: float | None = 0, default: Any = None) -> Decimal | None:
    value = args.get(key, default)
    if value is None:
        if required:
            raise ToolError(f"'{key}' is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ToolError(f"'{key}' must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ToolError(f"'{key}' must be a number, not {value!r}") from None
    if not number.is_finite():
        raise ToolError(f"'{key}' must be a finite number")
    if minimum is not None and number < Decimal(str(minimum)):
        raise ToolError(f"'{key}' must be at least {minimum}")
    return number


def _round(value: Decimal, places: int = 2) -> float:
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def _matches(query: str, *fields: str) -> bool:
    haystack = " ".join(fields).lower()
    return all(word in haystack for word in _words(query))


# --------------------------------------------------------------------------- lookups

def lookup_cost_code(args: dict[str, Any]) -> dict[str, Any]:
    query = _text(args, "query", required=False)
    division = _text(args, "division", required=False)
    limit = int(_number(args, "limit", required=False, minimum=1, default=15))
    if not query and not division:
        raise ToolError("give a 'query' (words from the scope, e.g. 'slab on grade') or a two-digit 'division'")
    if division and not re.fullmatch(r"\d{1,2}", division):
        raise ToolError("'division' must be a CSI division number such as '03' or '9'")
    division = division.zfill(2) if division else ""
    hits = [
        r
        for r in _rows("cost-codes.csv")
        if (not division or r["csi_division"] == division) and (not query or _matches(query, r["cost_code"], r["description"], r["notes"]))
    ]
    return {
        "count": len(hits),
        "matches": hits[: min(limit, 50)],
        "note": "A starter taxonomy with its own numbering, mapped to CSI division titles only. Map it to your accounting system's codes.",
        "source": SOURCE,
    }


def define_term(args: dict[str, Any]) -> dict[str, Any]:
    term = _text(args, "term")
    rows = _rows("glossary.csv")
    wanted = term.lower()

    def names(row: dict[str, str]) -> list[str]:
        full = row["term"].lower()
        short = full.split(" (")[0]
        inner = re.findall(r"\(([^)]+)\)", full)
        return [full, short, *inner]

    exact = [r for r in rows if wanted in names(r)]
    partial = [r for r in rows if r not in exact and _matches(term, r["term"])]
    in_text = [r for r in rows if r not in exact and r not in partial and _matches(term, r["definition"])]
    found = (exact + partial + in_text)[:5]
    if not found:
        raise ToolError(f"no glossary entry for {term!r}. The glossary has {len(rows)} terms; try a shorter or more general word")
    return {"matches": found, "source": SOURCE}


def waste_factor(args: dict[str, Any]) -> dict[str, Any]:
    material = _text(args, "material")
    rows = _rows("waste-factors.csv")
    hits = [r for r in rows if _matches(material, r["material"])]
    if not hits:
        raise ToolError(f"no waste factor for {material!r}. Available: {', '.join(r['material'] for r in rows)}")
    return {
        "matches": [
            {
                "material": r["material"],
                "planning_low_percent": float(r["planning_low_percent"]),
                "planning_high_percent": float(r["planning_high_percent"]),
                "basis": r["basis"],
                "drivers": r["drivers"],
                "notes": r["notes"],
            }
            for r in hits
        ],
        "note": PLANNING_NOTE,
        "source": SOURCE,
    }


def convert_units(args: dict[str, Any]) -> dict[str, Any]:
    value = _number(args, "value", minimum=None)
    src = _text(args, "from_unit").upper()
    dst = _text(args, "to_unit").upper()
    rows = [r for r in _rows("unit-conversions.csv") if r["category"] in LINEAR_CATEGORIES]
    units = sorted({r["from_unit"] for r in rows} | {r["to_unit"] for r in rows})

    for r in _rows("unit-conversions.csv"):
        if r["from_unit"] == src and r["to_unit"] == dst and Decimal(r["factor"]) == 0:
            raise ToolError(f"{src} cannot be converted to {dst}: {r['notes']}")

    direct = next((r for r in rows if r["from_unit"] == src and r["to_unit"] == dst), None)
    reverse = next((r for r in rows if r["from_unit"] == dst and r["to_unit"] == src), None)
    # 1/27 stored to six places makes 27 CF come out as 0.999999 CY; divide by an exact reverse factor instead
    if direct and reverse and reverse["notes"].startswith("Exact") and not direct["notes"].startswith("Exact") and Decimal(reverse["factor"]) != 0:
        direct = None
    if direct:
        factor, row, how = Decimal(direct["factor"]), direct, "direct"
    else:
        if not reverse or Decimal(reverse["factor"]) == 0:
            raise ToolError(f"no conversion from {src} to {dst}. Units available: {', '.join(units)}")
        factor, row, how = 1 / Decimal(reverse["factor"]), reverse, "reciprocal of the listed factor"
    result = value * factor
    return {
        "value": float(value),
        "from_unit": src,
        "to_unit": dst,
        "result": float(result.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP).normalize()),
        "factor": float(factor.quantize(Decimal("0.00000001")).normalize()),
        "method": how,
        "category": row["category"],
        "notes": row["notes"],
        "source": SOURCE,
    }


# --------------------------------------------------------------------------- calculators

def concrete_volume(args: dict[str, Any]) -> dict[str, Any]:
    length = _number(args, "length_ft")
    width = _number(args, "width_ft")
    depth_in = _number(args, "depth_in")
    count = _number(args, "count", required=False, minimum=1, default=1)
    waste = _number(args, "waste_percent", required=False)
    if 0 in (length, width, depth_in):
        raise ToolError("length_ft, width_ft and depth_in must all be greater than zero")
    cubic_feet = length * width * (depth_in / 12) * count
    cubic_yards = cubic_feet / 27
    out: dict[str, Any] = {
        "inputs": {"length_ft": float(length), "width_ft": float(width), "depth_in": float(depth_in), "count": int(count)},
        "neat_volume_cf": _round(cubic_feet, 2),
        "neat_volume_cy": _round(cubic_yards, 2),
    }
    if waste is not None:
        out["waste_percent"] = float(waste)
        out["order_volume_cy"] = _round(cubic_yards * (1 + waste / 100), 2)
    else:
        row = next(r for r in _rows("waste-factors.csv") if r["material"] == "Concrete")
        low, high = Decimal(row["planning_low_percent"]), Decimal(row["planning_high_percent"])
        out["waste_percent_range"] = [float(low), float(high)]
        out["order_volume_cy_range"] = [_round(cubic_yards * (1 + low / 100), 2), _round(cubic_yards * (1 + high / 100), 2)]
        out["waste_drivers"] = row["drivers"]
    out["note"] = (
        "Neat volume of a rectangular element (slab, footing, wall). Actual thickness varies with subgrade and "
        "formwork; round the order up to the supplier's increment. " + PLANNING_NOTE
    )
    out["source"] = SOURCE
    return out


def earthwork_volume(args: dict[str, Any]) -> dict[str, Any]:
    bank = _number(args, "bank_cy")
    material = _text(args, "material")
    rows = _rows("earthwork-volume-factors.csv")
    row = next((r for r in rows if _matches(material, r["material"])), None)
    if not row:
        raise ToolError(f"no factors for {material!r}. Available: {', '.join(r['material'] for r in rows)}")
    swell = [Decimal(row["bank_to_loose_low_percent"]), Decimal(row["bank_to_loose_high_percent"])]
    shrink = [Decimal(row["compacted_shrink_low_percent"]), Decimal(row["compacted_shrink_high_percent"])]
    return {
        "material": row["material"],
        "bank_cy": float(bank),
        "loose_cy_range": [_round(bank * (1 + s / 100), 1) for s in swell],
        "compacted_cy_range": [_round(bank * (1 - s / 100), 1) for s in reversed(shrink)],
        "swell_percent_range": [float(s) for s in swell],
        "shrink_percent_range": [float(s) for s in shrink],
        "notes": row["notes"],
        "explanation": "Loose volume is what the trucks haul; compacted volume is what the excavated bank volume becomes once placed and compacted.",
        "note": "Soil factors vary widely with moisture and handling. Use the geotechnical report where there is one. " + PLANNING_NOTE,
        "source": SOURCE,
    }


def markup_margin(args: dict[str, Any]) -> dict[str, Any]:
    cost = _number(args, "cost")
    markup = _number(args, "markup_percent", required=False)
    margin = _number(args, "margin_percent", required=False)
    if (markup is None) == (margin is None):
        raise ToolError("give exactly one of 'markup_percent' or 'margin_percent'")
    if margin is not None and margin >= 100:
        raise ToolError("'margin_percent' must be below 100")
    if cost == 0:
        raise ToolError("'cost' must be greater than zero")
    price = cost * (1 + markup / 100) if markup is not None else cost / (1 - margin / 100)
    profit = price - cost
    return {
        "cost": _round(cost),
        "price": _round(price),
        "profit": _round(profit),
        "markup_percent": _round(profit / cost * 100, 2),
        "margin_percent": _round(profit / price * 100, 2),
        "note": "Markup is profit as a share of cost; margin is profit as a share of price. A 20% markup is a 16.67% margin.",
    }


# --------------------------------------------------------------------------- definitions

def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_cost_code",
        "title": "Look up a cost code",
        "description": "Search a starter construction cost code list (about 140 codes, mapped to CSI divisions) by scope words or by division. Returns code, description, CSI division, cost type, unit and notes.",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Words describing the scope, e.g. 'gypsum board' or 'slab on grade'"},
                "division": {"type": "string", "description": "Two-digit CSI division, e.g. '09'"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15},
            },
            [],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "define_term",
        "title": "Define a construction term",
        "description": "Look up a construction term or abbreviation (e.g. 'retainage', 'RFI', 'AHJ') in a glossary of contract, cost control, field, safety and finance terms.",
        "inputSchema": _schema({"term": {"type": "string", "description": "The term or abbreviation"}}, ["term"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "waste_factor",
        "title": "Material waste factor",
        "description": "Planning waste range (low and high percent) for a construction material, with its basis and the drivers that push it up or down.",
        "inputSchema": _schema({"material": {"type": "string", "description": "e.g. 'gypsum board', 'concrete', 'tile'"}}, ["material"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "convert_units",
        "title": "Convert units",
        "description": "Convert between common construction units: LF/FT/M, SF/SY/SQ/ACRE/M2, CF/CY/M3/GAL, LB/TON/TONNE, PSI/FT_HEAD, PSF/KSF, PCF/KG_M3, KWH/BTU, HP/W, TON_REFRIG/BTU_HR. Refuses conversions that need more information, such as LF to SF.",
        "inputSchema": _schema(
            {
                "value": {"type": "number"},
                "from_unit": {"type": "string", "description": "Unit code, e.g. 'CY'"},
                "to_unit": {"type": "string", "description": "Unit code, e.g. 'M3'"},
            },
            ["value", "from_unit", "to_unit"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "concrete_volume",
        "title": "Concrete volume",
        "description": "Neat and order volume of concrete for rectangular slabs, footings or walls, in cubic feet and cubic yards. Applies a given waste percent, or the planning waste range for concrete.",
        "inputSchema": _schema(
            {
                "length_ft": {"type": "number", "exclusiveMinimum": 0},
                "width_ft": {"type": "number", "exclusiveMinimum": 0, "description": "Width, or wall height for a wall"},
                "depth_in": {"type": "number", "exclusiveMinimum": 0, "description": "Thickness in inches"},
                "count": {"type": "integer", "minimum": 1, "default": 1, "description": "Number of identical elements"},
                "waste_percent": {"type": "number", "minimum": 0, "description": "Optional; defaults to the planning range"},
            },
            ["length_ft", "width_ft", "depth_in"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "earthwork_volume",
        "title": "Earthwork swell and shrink",
        "description": "Convert a bank (in-place) volume of soil to loose (hauled) and compacted volumes, as ranges, for common earth, sand and gravel, clay, silty clay, blasted rock, topsoil or crushed stone.",
        "inputSchema": _schema(
            {
                "bank_cy": {"type": "number", "minimum": 0, "description": "Bank volume in cubic yards"},
                "material": {"type": "string", "description": "e.g. 'clay', 'common earth'"},
            },
            ["bank_cy", "material"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "markup_margin",
        "title": "Markup and margin",
        "description": "From a cost and either a markup percent or a margin percent, compute the price, the profit and the other percentage. Use it to avoid the common mistake of treating markup and margin as the same number.",
        "inputSchema": _schema(
            {
                "cost": {"type": "number", "exclusiveMinimum": 0},
                "markup_percent": {"type": "number", "minimum": 0},
                "margin_percent": {"type": "number", "minimum": 0, "exclusiveMaximum": 100},
            },
            ["cost"],
        ),
        "annotations": READ_ONLY,
    },
]

HANDLERS = {
    "lookup_cost_code": lookup_cost_code,
    "define_term": define_term,
    "waste_factor": waste_factor,
    "convert_units": convert_units,
    "concrete_volume": concrete_volume,
    "earthwork_volume": earthwork_volume,
    "markup_margin": markup_margin,
}
