"""The tools: lookups over bundled reference data, and a few plain calculators.

All data comes from the files in ``data/``, copied from the CC BY 4.0 construction-data repository
(see ``data/SOURCE.md``). Nothing is fetched, written or remembered between calls.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from .protocol import ToolError
from .schedule import RELATIONSHIPS, open_ends, run_cpm, workday

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


# --------------------------------------------------------------------------- more lookups

def pay_unit(args: dict[str, Any]) -> dict[str, Any]:
    scope = _text(args, "scope")
    rows = _rows("pay-units.csv")
    hits = [r for r in rows if _matches(scope, r["scope_item"], r["trade"])]
    if not hits:
        raise ToolError(f"no pay unit for {scope!r}. Try a broader word; trades covered: {', '.join(sorted({r['trade'] for r in rows}))}")
    return {"matches": hits[:10], "note": "Typical pay unit and measurement basis. Your contract's measurement clauses govern.", "source": SOURCE}


def trade_sequence(args: dict[str, Any]) -> dict[str, Any]:
    query = _text(args, "query", required=False)
    rows = _rows("trade-sequence.csv")
    hits = [r for r in rows if not query or _matches(query, r["trade"], r["activity"])]
    if not hits:
        raise ToolError(f"nothing in the typical sequence matches {query!r}")
    return {
        "steps": hits,
        "note": "A typical commercial building sequence, as a logic skeleton. Real sequences depend on the building, the contract and the site.",
        "source": SOURCE,
    }


def metric_formula(args: dict[str, Any]) -> dict[str, Any]:
    query = _text(args, "metric")
    rows = _rows("metric-formulas.csv")
    hits = [r for r in rows if _matches(query, r["metric"])] or [r for r in rows if _matches(query, r["metric"], r["interpretation"])]
    if not hits:
        raise ToolError(f"no metric matches {query!r}. Available: {', '.join(r['metric'] for r in rows)}")
    return {"matches": hits[:6], "source": SOURCE}


# --------------------------------------------------------------------------- more calculators

def earned_value(args: dict[str, Any]) -> dict[str, Any]:
    bac = _number(args, "bac")
    ev = _number(args, "ev")
    ac = _number(args, "ac")
    pv = _number(args, "pv", required=False)
    if bac == 0 or ac == 0:
        raise ToolError("'bac' and 'ac' must be greater than zero")
    if ev > bac:
        raise ToolError("'ev' (earned value) cannot exceed 'bac' (budget at completion)")
    cpi = ev / ac
    out: dict[str, Any] = {
        "cost_variance": _round(ev - ac),
        "cpi": _round(cpi, 3),
        "percent_complete_earned": _round(ev / bac * 100, 1),
        "percent_of_budget_spent": _round(ac / bac * 100, 1),
    }
    if cpi > 0:
        eac = bac / cpi
        out["eac_if_performance_continues"] = _round(eac)
        out["etc_if_performance_continues"] = _round(eac - ac)
        out["variance_at_completion"] = _round(bac - eac)
    out["eac_if_variance_was_one_off"] = _round(ac + (bac - ev))
    if bac != ac:
        out["tcpi_to_finish_on_budget"] = _round((bac - ev) / (bac - ac), 3)
    if pv is not None:
        out["schedule_variance"] = _round(ev - pv)
        if pv > 0:
            out["spi"] = _round(ev / pv, 3)
    warnings = []
    if ev / bac < Decimal("0.15"):
        warnings.append("Less than 15% earned: CPI and EAC are unreliable this early.")
    if bac != ac and (bac - ev) / (bac - ac) > Decimal("1.1"):
        warnings.append("TCPI above 1.1: finishing on budget needs much better cost performance than achieved so far.")
    if ac >= bac:
        warnings.append("Actual cost has reached the budget; the remaining work is already over budget.")
    out["warnings"] = warnings
    out["note"] = "SPI is cost-weighted and says nothing about the critical path; check the schedule for that."
    return out


def retainage(args: dict[str, Any]) -> dict[str, Any]:
    billed = _number(args, "work_billed_to_date")
    pct = _number(args, "retainage_percent")
    released = _number(args, "released", required=False, default=0)
    paid = _number(args, "previously_paid", required=False)
    if pct > 100:
        raise ToolError("'retainage_percent' cannot exceed 100")
    held = billed * pct / 100
    if released > held:
        raise ToolError(f"'released' ({released}) is more than the retainage held ({_round(held)})")
    out = {
        "retainage_held_gross": _round(held),
        "retainage_released": _round(released),
        "retainage_outstanding": _round(held - released),
        "earned_less_retainage": _round(billed - held + released),
    }
    if paid is not None:
        out["due_this_application"] = _round(billed - held + released - paid)
    out["note"] = "Contracts often reduce retainage at a milestone (for example, 50% complete). Apply your contract's terms."
    return out


def _material_factor(key: str) -> Decimal:
    row = next(r for r in _rows("unit-conversions.csv") if r["from_unit"] == key)
    return Decimal(row["factor"])


def _waste(material: str) -> tuple[Decimal, Decimal]:
    row = next(r for r in _rows("waste-factors.csv") if r["material"] == material)
    return Decimal(row["planning_low_percent"]), Decimal(row["planning_high_percent"])


def rebar_weight(args: dict[str, Any]) -> dict[str, Any]:
    size = _number(args, "bar_size", minimum=3)
    length = _number(args, "length_ft")
    count = _number(args, "count", required=False, minimum=1, default=1)
    if size != size.to_integral_value() or not 3 <= size <= 11:
        raise ToolError("'bar_size' must be a US bar number from 3 to 11 (for example 5 for a #5 bar)")
    per_ft = _material_factor(f"REBAR_{int(size)}")
    pounds = per_ft * length * count
    low, high = _waste("Reinforcing steel")
    return {
        "bar": f"#{int(size)}",
        "lb_per_ft": float(per_ft),
        "total_length_ft": _round(length * count, 1),
        "weight_lb": _round(pounds, 1),
        "weight_tons": _round(pounds / 2000, 3),
        "with_waste_tons_range": [_round(pounds * (1 + low / 100) / 2000, 3), _round(pounds * (1 + high / 100) / 2000, 3)],
        "note": "Nominal ASTM weights. Include laps, hooks and development lengths in the length you give. " + PLANNING_NOTE,
        "source": SOURCE,
    }


MASONRY = {"cmu": ("CMU_8X8X16", "Masonry block", "8x8x16 concrete masonry units"), "brick": ("BRICK_MODULAR", "Brick", "modular brick")}


def masonry_units(args: dict[str, Any]) -> dict[str, Any]:
    area = _number(args, "wall_area_sf")
    openings = _number(args, "openings_sf", required=False, default=0)
    unit = _text(args, "unit").lower()
    if unit not in MASONRY:
        raise ToolError("'unit' must be 'cmu' (8x8x16 block) or 'brick' (modular brick)")
    if openings >= area:
        raise ToolError("'openings_sf' must be less than 'wall_area_sf'")
    key, waste_name, label = MASONRY[unit]
    per_sf = _material_factor(key)
    net = area - openings
    units = net * per_sf
    low, high = _waste(waste_name)
    return {
        "unit": label,
        "net_wall_area_sf": _round(net, 1),
        "units_per_sf": float(per_sf),
        "units_neat": int(units.to_integral_value(rounding=ROUND_HALF_UP)),
        "units_with_waste_range": [int((units * (1 + p / 100)).to_integral_value(rounding="ROUND_CEILING")) for p in (low, high)],
        "note": "Single wythe, nominal joints. Add special units (bond beam, lintel, corner) separately. " + PLANNING_NOTE,
        "source": SOURCE,
    }


def asphalt_tonnage(args: dict[str, Any]) -> dict[str, Any]:
    area_sf = _number(args, "area_sf")
    thickness = _number(args, "thickness_in")
    if area_sf == 0 or thickness == 0:
        raise ToolError("'area_sf' and 'thickness_in' must be greater than zero")
    area_sy = area_sf / 9
    tons = area_sy * thickness * _material_factor("ASPHALT_SY_IN")
    low, high = _waste("Asphalt concrete")
    return {
        "area_sy": _round(area_sy, 1),
        "compacted_thickness_in": float(thickness),
        "tons_neat": _round(tons, 1),
        "tons_with_waste_range": [_round(tons * (1 + low / 100), 1), _round(tons * (1 + high / 100), 1)],
        "note": "Uses about 110 lb per square yard per inch of compacted mix. Mix density varies: use the plant's figure when you have it. " + PLANNING_NOTE,
        "source": SOURCE,
    }


# --------------------------------------------------------------------------- schedule and money

MAX_ACTIVITIES = 500
MAX_LINES = 300


def _list(args: dict[str, Any], key: str, limit: int) -> list[Any]:
    value = args.get(key)
    if not isinstance(value, list) or not value:
        raise ToolError(f"'{key}' must be a non-empty list")
    if len(value) > limit:
        raise ToolError(f"'{key}' has {len(value)} items; the limit is {limit}")
    return value


def _predecessor(raw: Any, owner: str) -> tuple[str, str, Decimal]:
    """A predecessor is an id ("A", meaning finish-to-start with no lag) or {"id", "type", "lag"}."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip(), "FS", Decimal(0)
    if not isinstance(raw, dict):
        raise ToolError(f"activity {owner!r}: each predecessor must be an id or an object with 'id', 'type' and 'lag'")
    pred = _text(raw, "id")
    kind = (_text(raw, "type", required=False) or "FS").upper()
    if kind not in RELATIONSHIPS:
        raise ToolError(f"activity {owner!r}: relationship type must be one of {', '.join(RELATIONSHIPS)}, not {kind!r}")
    lag = _number(raw, "lag", required=False, minimum=None, default=0)
    return pred, kind, lag


def critical_path(args: dict[str, Any]) -> dict[str, Any]:
    raw = _list(args, "activities", MAX_ACTIVITIES)
    near = _number(args, "near_critical_days", required=False, default=5)
    acts: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ToolError("each activity must be an object with 'id', 'duration' and optional 'predecessors'")
        key = _text(item, "id")
        if key in acts:
            raise ToolError(f"activity id {key!r} appears more than once")
        preds = item.get("predecessors") or []
        if not isinstance(preds, list):
            raise ToolError(f"activity {key!r}: 'predecessors' must be a list")
        acts[key] = {
            "id": key,
            "name": _text(item, "name", required=False),
            "duration": _number(item, "duration"),
            "preds": [_predecessor(p, key) for p in preds],
        }
    cpm = run_cpm(acts)
    es, ef, lf, finish, order = cpm["es"], cpm["ef"], cpm["lf"], cpm["finish"], cpm["order"]

    start_text = _text(args, "start_date", required=False)
    workdays_only = args.get("workdays_only", True)
    if not isinstance(workdays_only, bool):
        raise ToolError("'workdays_only' must be true or false")
    start_day = None
    if start_text:
        try:
            start_day = date.fromisoformat(start_text)
        except ValueError:
            raise ToolError("'start_date' must be an ISO date such as 2026-03-02") from None
        values = [a["duration"] for a in acts.values()] + [lag for a in acts.values() for _, _, lag in a["preds"]]
        if any(v != v.to_integral_value() for v in values):
            raise ToolError("dates need whole-day durations and lags; leave out 'start_date' to work in fractional days")

    def num(v: Decimal) -> float:
        return float(v.normalize()) if v == v.to_integral_value() else _round(v, 2)

    rows = []
    for k in sorted(acts, key=lambda k: (es[k], order.index(k))):
        a = acts[k]
        total = cpm["total_float"][k]
        row: dict[str, Any] = {
            "id": k,
            **({"name": a["name"]} if a["name"] else {}),
            "duration": num(a["duration"]),
            "early_start": num(es[k]),
            "early_finish": num(ef[k]),
            "late_start": num(lf[k] - a["duration"]),
            "late_finish": num(lf[k]),
            "total_float": num(total),
            "free_float": num(cpm["free_float"][k]),
            "critical": total <= 0,
        }
        if start_day is not None:
            row["start_date"] = workday(start_day, int(es[k]), workdays_only).isoformat()
            last = int(ef[k]) - 1 if a["duration"] > 0 else int(ef[k])
            row["finish_date"] = workday(start_day, last, workdays_only).isoformat()
        rows.append(row)

    open_starts, open_finishes = open_ends(acts, cpm["succs"])
    warnings = []
    if len(open_starts) > 1:
        warnings.append(f"{len(open_starts)} activities have no predecessor driving their start ({', '.join(open_starts[:10])}). Check that each really can start on day 0.")
    if len(open_finishes) > 1:
        warnings.append(f"{len(open_finishes)} activities have no successor depending on their finish ({', '.join(open_finishes[:10])}). Open ends overstate float; tie them to a later activity or a finish milestone.")
    if any(lag < 0 for a in acts.values() for _, _, lag in a["preds"]):
        warnings.append("Negative lags (leads) are in use. They hide logic; many scheduling specs restrict them.")
    out: dict[str, Any] = {
        "project_duration": num(finish),
        "critical_path": cpm["chain"],
        "critical_activities": [r["id"] for r in rows if r["critical"]],
        "near_critical": [r["id"] for r in rows if not r["critical"] and Decimal(str(r["total_float"])) <= near],
        "activities": rows,
        "warnings": warnings,
        "note": (
            "Durations, lags and float are in the units you gave (normally working days); day 0 is the start. No calendars, "
            "constraints or resource limits are applied: check the result in your scheduling software."
        ),
    }
    if start_day is not None:
        out["finish_date"] = workday(start_day, max(int(finish) - 1, 0), workdays_only).isoformat()
        out["calendar"] = "Monday to Friday, no holidays" if workdays_only else "every calendar day"
    return out


def pay_application(args: dict[str, Any]) -> dict[str, Any]:
    raw = _list(args, "lines", MAX_LINES)
    work_pct = _number(args, "retainage_percent")
    stored_pct = _number(args, "stored_materials_retainage_percent", required=False, default=work_pct)
    previous = _number(args, "previous_certificates", required=False, default=0)
    if work_pct > 100 or stored_pct > 100:
        raise ToolError("retainage percentages cannot exceed 100")
    cent = Decimal("0.01")
    lines, warnings = [], []
    totals = {k: Decimal(0) for k in ("scheduled", "previous", "this", "stored", "completed", "retainage_work", "retainage_stored")}
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ToolError(f"line {n}: each line must be an object")
        label = _text(item, "description", required=False) or f"line {n}"
        sv = _number(item, "scheduled_value")
        prev = _number(item, "previous_completed", required=False, default=0)
        this = _number(item, "this_period", required=False, default=0, minimum=None)
        stored = _number(item, "stored_materials", required=False, default=0)
        work = prev + this
        if work < 0:
            raise ToolError(f"{label}: 'this_period' credits more than was previously completed")
        completed = work + stored
        ret_work = (work * work_pct / 100).quantize(cent, rounding=ROUND_HALF_UP)
        ret_stored = (stored * stored_pct / 100).quantize(cent, rounding=ROUND_HALF_UP)
        if completed > sv:
            warnings.append(f"{label}: completed and stored ({_round(completed)}) exceeds the scheduled value ({_round(sv)}). Overbilled, or a change order is missing from the schedule of values.")
        lines.append({
            "description": label,
            "scheduled_value": _round(sv),
            "previous_completed": _round(prev),
            "this_period": _round(this),
            "stored_materials": _round(stored),
            "completed_and_stored_to_date": _round(completed),
            "percent_complete": _round(completed / sv * 100, 1) if sv else None,
            "balance_to_finish": _round(sv - completed),
            "retainage": _round(ret_work + ret_stored),
        })
        for key, value in (("scheduled", sv), ("previous", prev), ("this", this), ("stored", stored), ("completed", completed), ("retainage_work", ret_work), ("retainage_stored", ret_stored)):
            totals[key] += value

    retainage_total = totals["retainage_work"] + totals["retainage_stored"]
    earned = totals["completed"] - retainage_total
    due = earned - previous
    if due < 0:
        warnings.append("Earned less retainage is below the previous certificates: this application is a credit. Check for over-payment or a billing reversal.")
    return {
        "lines": lines,
        "summary": {
            "contract_sum_to_date": _round(totals["scheduled"]),
            "total_completed_and_stored_to_date": _round(totals["completed"]),
            "retainage_on_completed_work": _round(totals["retainage_work"]),
            "retainage_on_stored_materials": _round(totals["retainage_stored"]),
            "total_retainage": _round(retainage_total),
            "total_earned_less_retainage": _round(earned),
            "less_previous_certificates": _round(previous),
            "current_payment_due": _round(due),
            "balance_to_finish_including_retainage": _round(totals["scheduled"] - earned),
            "percent_complete": _round(totals["completed"] / totals["scheduled"] * 100, 1) if totals["scheduled"] else None,
        },
        "warnings": warnings,
        "note": (
            "Laid out like a standard schedule of values continuation sheet and payment summary. Scheduled values should "
            "already include approved change orders. Retainage is rounded to the cent per line. Your contract's retainage "
            "and stored-materials terms govern."
        ),
    }


def change_order_markup(args: dict[str, Any]) -> dict[str, Any]:
    labor = _number(args, "labor", required=False, default=0)
    material = _number(args, "material", required=False, default=0)
    equipment = _number(args, "equipment", required=False, default=0)
    sub = _number(args, "subcontract", required=False, default=0)
    burden = _number(args, "labor_burden_percent", required=False, default=0)
    overhead = _number(args, "overhead_percent", required=False, default=0)
    profit = _number(args, "profit_percent", required=False, default=0)
    sub_markup = _number(args, "subcontract_markup_percent", required=False)
    bond = _number(args, "bond_percent", required=False, default=0)
    insurance = _number(args, "insurance_percent", required=False, default=0)
    compound = args.get("profit_on_overhead", True)
    if not isinstance(compound, bool):
        raise ToolError("'profit_on_overhead' must be true or false")
    if labor + material + equipment + sub == 0:
        raise ToolError("give at least one of 'labor', 'material', 'equipment' or 'subcontract'")

    burden_amount = labor * burden / 100
    own = labor + burden_amount + material + equipment
    oh = own * overhead / 100
    pr = (own + oh) * profit / 100 if compound else own * profit / 100
    if sub_markup is None:
        sub_oh = sub * overhead / 100
        sub_pr = (sub + sub_oh) * profit / 100 if compound else sub * profit / 100
        sub_fee = sub_oh + sub_pr
    else:
        sub_fee = sub * sub_markup / 100
    subtotal = own + oh + pr + sub + sub_fee
    bond_amount = subtotal * bond / 100
    insurance_amount = subtotal * insurance / 100
    total = subtotal + bond_amount + insurance_amount
    direct = own + sub  # burdened labor is a cost, not a markup
    steps = [
        ("Labor", labor), ("Labor burden", burden_amount), ("Material", material), ("Equipment", equipment),
        ("Own-forces overhead", oh), ("Own-forces profit", pr), ("Subcontract", sub), ("Markup on subcontract", sub_fee),
        ("Subtotal", subtotal), ("Bond", bond_amount), ("Insurance", insurance_amount), ("Change order total", total),
    ]
    return {
        "breakdown": [{"item": name, "amount": _round(amount)} for name, amount in steps if amount or name in ("Subtotal", "Change order total")],
        "total": _round(total),
        "direct_cost_with_burden": _round(direct),
        "effective_markup_on_direct_cost_percent": _round((total - direct) / direct * 100, 2),
        "order_of_markups": (
            "Burden on labor; overhead on own-forces cost; profit on cost plus overhead"
            + ("" if compound else " (here: on cost only)")
            + "; subcontract marked up separately; bond and insurance on the subtotal."
        ),
        "note": (
            "Contracts usually fix which markups are allowed, on what base, and in what order, and often cap them, with a "
            "lower rate on subcontracted work. Check the changes clause before pricing; the same percentages in a different "
            "order give a different total."
        ),
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
    {
        "name": "pay_unit",
        "title": "Pay unit for a scope item",
        "description": "The typical pay unit and measurement basis for a scope item (for example bulk excavation is paid by the CY, bank measure), with what is usually excluded.",
        "inputSchema": _schema({"scope": {"type": "string", "description": "e.g. 'excavation', 'curb', 'drywall'"}}, ["scope"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "trade_sequence",
        "title": "Typical trade sequence",
        "description": "A typical commercial building sequence: each step's trade, activity, usual predecessor, what drives its duration, and the constraint that usually delays it. Give a query to filter, or none for the whole sequence.",
        "inputSchema": _schema({"query": {"type": "string", "description": "Optional, e.g. 'drywall' or 'roofing'"}}, []),
        "annotations": READ_ONLY,
    },
    {
        "name": "metric_formula",
        "title": "Cost and schedule metric formula",
        "description": "The formula, inputs, interpretation and caveats for a cost or schedule metric: CPI, SPI, EAC, TCPI, over/under billing, DSO, float consumed and others.",
        "inputSchema": _schema({"metric": {"type": "string", "description": "e.g. 'CPI', 'estimate at completion', 'overbilling'"}}, ["metric"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "earned_value",
        "title": "Earned value analysis",
        "description": "From budget at completion, earned value and actual cost (and optionally planned value), compute cost and schedule variance, CPI, SPI, two estimates at completion, ETC, VAC and TCPI, with warnings when the numbers are not yet meaningful.",
        "inputSchema": _schema(
            {
                "bac": {"type": "number", "exclusiveMinimum": 0, "description": "Budget at completion"},
                "ev": {"type": "number", "minimum": 0, "description": "Earned value: budgeted cost of work performed"},
                "ac": {"type": "number", "exclusiveMinimum": 0, "description": "Actual cost to date"},
                "pv": {"type": "number", "minimum": 0, "description": "Optional planned value to date"},
            },
            ["bac", "ev", "ac"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "retainage",
        "title": "Retainage on a pay application",
        "description": "Retainage held, released and outstanding on work billed to date, and optionally the amount due this application after previous payments.",
        "inputSchema": _schema(
            {
                "work_billed_to_date": {"type": "number", "minimum": 0},
                "retainage_percent": {"type": "number", "minimum": 0, "maximum": 100},
                "released": {"type": "number", "minimum": 0, "default": 0},
                "previously_paid": {"type": "number", "minimum": 0},
            },
            ["work_billed_to_date", "retainage_percent"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "rebar_weight",
        "title": "Rebar weight",
        "description": "Weight of US reinforcing bars (#3 to #11) from bar size, length and count, in pounds and tons, with the planning waste range.",
        "inputSchema": _schema(
            {
                "bar_size": {"type": "integer", "minimum": 3, "maximum": 11, "description": "Bar number, e.g. 5 for #5"},
                "length_ft": {"type": "number", "exclusiveMinimum": 0, "description": "Length of one bar including laps and hooks"},
                "count": {"type": "integer", "minimum": 1, "default": 1},
            },
            ["bar_size", "length_ft"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "masonry_units",
        "title": "Masonry unit count",
        "description": "Number of 8x8x16 CMU or modular brick for a single-wythe wall area, less openings, with the planning waste range.",
        "inputSchema": _schema(
            {
                "wall_area_sf": {"type": "number", "exclusiveMinimum": 0},
                "unit": {"type": "string", "enum": ["cmu", "brick"]},
                "openings_sf": {"type": "number", "minimum": 0, "default": 0},
            },
            ["wall_area_sf", "unit"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "asphalt_tonnage",
        "title": "Asphalt tonnage",
        "description": "Tons of hot-mix asphalt for a paved area and compacted thickness, with the planning waste range.",
        "inputSchema": _schema(
            {
                "area_sf": {"type": "number", "exclusiveMinimum": 0},
                "thickness_in": {"type": "number", "exclusiveMinimum": 0, "description": "Compacted thickness in inches"},
            },
            ["area_sf", "thickness_in"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "critical_path",
        "title": "Critical path (CPM)",
        "description": "Run a critical path method forward and backward pass over a list of activities with durations and predecessors (FS, SS, FF or SF, with lags). Returns project duration, the driving critical path, near-critical activities, early and late dates, total and free float for each activity, and logic warnings such as open ends. Rejects loops and unknown predecessors. Optionally converts days to dates from a start date.",
        "inputSchema": _schema(
            {
                "activities": {
                    "type": "array",
                    "maxItems": MAX_ACTIVITIES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "duration": {"type": "number", "minimum": 0, "description": "Working days; 0 for a milestone"},
                            "predecessors": {
                                "type": "array",
                                "description": "Ids (finish-to-start, no lag) or objects such as {\"id\": \"A\", \"type\": \"SS\", \"lag\": 2}",
                                "items": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "type": {"type": "string", "enum": list(RELATIONSHIPS), "default": "FS"},
                                                "lag": {"type": "number", "default": 0, "description": "Negative for a lead"},
                                            },
                                            "required": ["id"],
                                        },
                                    ]
                                },
                            },
                        },
                        "required": ["id", "duration"],
                    },
                },
                "start_date": {"type": "string", "format": "date", "description": "Optional ISO start date; needs whole-day durations"},
                "workdays_only": {"type": "boolean", "default": True, "description": "Count Monday to Friday only when giving dates"},
                "near_critical_days": {"type": "number", "minimum": 0, "default": 5},
            },
            ["activities"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "pay_application",
        "title": "Pay application math",
        "description": "Check or build a progress payment from schedule of values lines: completed and stored to date, percent complete, balance to finish and retainage per line, and the summary (total earned less retainage, less previous certificates, current payment due, balance to finish including retainage). Supports a separate retainage rate on stored materials and flags lines billed over their scheduled value.",
        "inputSchema": _schema(
            {
                "lines": {
                    "type": "array",
                    "maxItems": MAX_LINES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "scheduled_value": {"type": "number", "minimum": 0},
                            "previous_completed": {"type": "number", "minimum": 0, "default": 0, "description": "Work completed on earlier applications"},
                            "this_period": {"type": "number", "default": 0, "description": "Work completed this period; negative for a credit"},
                            "stored_materials": {"type": "number", "minimum": 0, "default": 0, "description": "Materials presently stored, not yet installed"},
                        },
                        "required": ["scheduled_value"],
                    },
                },
                "retainage_percent": {"type": "number", "minimum": 0, "maximum": 100},
                "stored_materials_retainage_percent": {"type": "number", "minimum": 0, "maximum": 100, "description": "Defaults to retainage_percent"},
                "previous_certificates": {"type": "number", "minimum": 0, "default": 0, "description": "Total certified for payment on earlier applications"},
            },
            ["lines", "retainage_percent"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "change_order_markup",
        "title": "Change order markup",
        "description": "Price a change order from direct costs (labor, material, equipment, subcontract) by applying labor burden, overhead, profit, subcontract markup, bond and insurance in a stated order, so the total is reproducible. Returns the line-by-line build-up and the effective markup on direct cost.",
        "inputSchema": _schema(
            {
                "labor": {"type": "number", "minimum": 0},
                "material": {"type": "number", "minimum": 0},
                "equipment": {"type": "number", "minimum": 0},
                "subcontract": {"type": "number", "minimum": 0},
                "labor_burden_percent": {"type": "number", "minimum": 0, "description": "Payroll taxes, insurance and fringes on bare labor"},
                "overhead_percent": {"type": "number", "minimum": 0},
                "profit_percent": {"type": "number", "minimum": 0},
                "profit_on_overhead": {"type": "boolean", "default": True, "description": "Apply profit to cost plus overhead (true) or to cost only"},
                "subcontract_markup_percent": {"type": "number", "minimum": 0, "description": "Combined markup on subcontract cost; defaults to overhead then profit"},
                "bond_percent": {"type": "number", "minimum": 0},
                "insurance_percent": {"type": "number", "minimum": 0},
            },
            [],
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
    "pay_unit": pay_unit,
    "trade_sequence": trade_sequence,
    "metric_formula": metric_formula,
    "earned_value": earned_value,
    "retainage": retainage,
    "rebar_weight": rebar_weight,
    "masonry_units": masonry_units,
    "asphalt_tonnage": asphalt_tonnage,
    "critical_path": critical_path,
    "pay_application": pay_application,
    "change_order_markup": change_order_markup,
}


DATASETS = {
    "cost-codes.csv": "Starter cost code taxonomy mapped to CSI divisions",
    "glossary.csv": "Construction glossary: contracts, cost control, field, safety, finance",
    "waste-factors.csv": "Planning waste ranges by material, with drivers",
    "earthwork-volume-factors.csv": "Bank, loose and compacted volume factors by soil type",
    "unit-conversions.csv": "Unit conversions and material factors",
    "pay-units.csv": "Typical pay unit and measurement basis per scope item",
    "trade-sequence.csv": "Typical commercial trade sequence with predecessors and constraints",
    "metric-formulas.csv": "Cost and schedule metric formulas with caveats",
}

RESOURCES: list[dict[str, Any]] = [
    {
        "uri": f"construction-reference://data/{name}",
        "name": name,
        "title": title,
        "description": f"{title}. CSV, CC BY 4.0, from construction-data.",
        "mimeType": "text/csv",
        "read": (lambda n=name: (DATA / n).read_text(encoding="utf-8")),
    }
    for name, title in DATASETS.items()
]
