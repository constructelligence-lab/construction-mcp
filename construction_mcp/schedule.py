"""Schedules: the critical path engine, Primavera P6 (XER) and Microsoft Project (MSPDI XML) readers, and a
DCMA 14-point assessment.

The engine works in whatever unit the durations are in (working days for imported files) and applies logic only:
no calendars, constraints or resource levelling. The assessment says so wherever it matters, and uses the float
the scheduling software itself calculated whenever the file carries it.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from .protocol import ToolError

RELATIONSHIPS = ("FS", "SS", "FF", "SF")
ZERO = Decimal(0)
HIGH_DAYS = Decimal(44)  # DCMA's threshold for high float and high duration, in working days


# --------------------------------------------------------------------------- engine

def _start_from(kind: str, pred_es: Decimal, pred_ef: Decimal, lag: Decimal, duration: Decimal) -> Decimal:
    """The earliest start a link of this kind allows its successor."""
    return {"FS": pred_ef + lag, "SS": pred_es + lag, "FF": pred_ef + lag - duration, "SF": pred_es + lag - duration}[kind]


def run_cpm(acts: dict[str, dict[str, Any]], start_at: dict[str, Decimal] | None = None) -> dict[str, Any]:
    """Forward and backward pass. ``acts`` maps id -> {"duration": Decimal, "preds": [(id, type, lag)]}.

    ``start_at`` pins an activity's early start no earlier than a value (used for work already in progress).
    """
    for key, a in acts.items():
        for pred, _, _ in a["preds"]:
            if pred not in acts:
                raise ToolError(f"activity {key!r} has predecessor {pred!r}, which is not in the list")
            if pred == key:
                raise ToolError(f"activity {key!r} lists itself as a predecessor")

    # topological order (Kahn); whatever is left over sits on or behind a loop
    succs: dict[str, list[tuple[str, str, Decimal]]] = {k: [] for k in acts}
    waiting = {k: len(a["preds"]) for k, a in acts.items()}
    for key, a in acts.items():
        for pred, kind, lag in a["preds"]:
            succs[pred].append((key, kind, lag))
    order = [k for k in acts if waiting[k] == 0]
    for k in order:
        for s, _, _ in succs[k]:
            waiting[s] -= 1
            if waiting[s] == 0:
                order.append(s)
    if len(order) < len(acts):
        stuck = [k for k in acts if waiting[k] > 0]
        raise ToolError(f"the logic has a loop; these activities are on it or wait on it: {', '.join(stuck[:20])}")

    es: dict[str, Decimal] = {}
    for k in order:
        a = acts[k]
        start = (start_at or {}).get(k, ZERO)
        for pred, kind, lag in a["preds"]:
            start = max(start, _start_from(kind, es[pred], es[pred] + acts[pred]["duration"], lag, a["duration"]))
        es[k] = start
    ef = {k: es[k] + acts[k]["duration"] for k in acts}
    finish = max(ef.values()) if ef else ZERO

    lf: dict[str, Decimal] = {}
    for k in reversed(order):
        a, late = acts[k], finish
        for s, kind, lag in succs[k]:
            sls, slf = lf[s] - acts[s]["duration"], lf[s]
            late = min(late, {"FS": sls - lag, "SS": sls - lag + a["duration"], "FF": slf - lag, "SF": slf - lag + a["duration"]}[kind])
        lf[k] = late

    total_float = {k: lf[k] - acts[k]["duration"] - es[k] for k in acts}
    free = {}
    for k in acts:
        if not succs[k]:
            free[k] = finish - ef[k]
        else:
            free[k] = min({"FS": es[s] - ef[k] - lag, "SS": es[s] - es[k] - lag, "FF": ef[s] - ef[k] - lag, "SF": ef[s] - es[k] - lag}[kind] for s, kind, lag in succs[k])

    # the driving chain: walk back from the latest-finishing critical activity through links with no slack
    chain: list[str] = []
    ends = [k for k in order if ef[k] == finish and total_float[k] <= 0]
    current = ends[-1] if ends else None
    while current is not None:
        chain.append(current)
        a, nxt = acts[current], None
        for pred, kind, lag in a["preds"]:
            driven = _start_from(kind, es[pred], ef[pred], lag, a["duration"])
            if driven == es[current] and total_float[pred] <= 0:
                nxt = pred
                break
        current = nxt
    chain.reverse()
    return {"order": order, "succs": succs, "es": es, "ef": ef, "lf": lf, "finish": finish, "total_float": total_float, "free_float": free, "chain": chain}


def open_ends(acts: dict[str, dict[str, Any]], succs: dict[str, list[tuple[str, str, Decimal]]]) -> tuple[list[str], list[str]]:
    """Activities whose start nothing drives (no FS or SS in) and whose finish nothing depends on (no FS or FF out)."""
    starts = [k for k, a in acts.items() if not any(kind in ("FS", "SS") for _, kind, _ in a["preds"])]
    finishes = [k for k in acts if not any(kind in ("FS", "FF") for _, kind, _ in succs[k])]
    return starts, finishes


def workday(start: date, offset: int, workdays_only: bool = True) -> date:
    """The date ``offset`` working days from ``start`` (Monday to Friday, no holidays)."""
    if not workdays_only:
        return start + timedelta(days=offset)
    day = start
    while day.weekday() >= 5:
        day += timedelta(days=1)
    step = 1 if offset >= 0 else -1
    for _ in range(abs(offset)):
        day += timedelta(days=step)
        while day.weekday() >= 5:
            day += timedelta(days=step)
    return day


def workdays_between(start: date, end: date) -> int:
    """Monday-to-Friday days from ``start`` up to (not including) ``end``; negative if ``end`` is earlier."""
    if end < start:
        return -workdays_between(end, start)
    weeks, rest = divmod((end - start).days, 7)
    count = weeks * 5
    for i in range(rest):
        if (start + timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


# --------------------------------------------------------------------------- readers

def _date(text: str | None) -> date | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(datetime(2000, 1, 1).strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def _dec(text: str | None, default: Decimal = ZERO) -> Decimal:
    try:
        return Decimal(text) if text not in (None, "") else default
    except Exception:
        return default


# P6 constraint codes. DCMA counts the ones that stop logic from moving a date as hard.
P6_HARD = {"CS_MSO": "Must start on", "CS_MEO": "Must finish on", "CS_MSOB": "Start on or before", "CS_MEOB": "Finish on or before", "CS_MANDSTART": "Mandatory start", "CS_MANDFIN": "Mandatory finish"}
MSP_HARD = {"2": "Must start on", "3": "Must finish on", "5": "Start no later than", "7": "Finish no later than"}
MSP_LINK = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


def parse_xer(text: str, project: str | None = None) -> dict[str, Any]:
    """Read a Primavera P6 XER export: tab-separated tables introduced by %T, %F (fields) and %R (rows)."""
    tables: dict[str, list[dict[str, str]]] = {}
    table, fields = None, []
    for line in text.splitlines():
        parts = line.rstrip("\r").split("\t")
        tag = parts[0]
        if tag == "%T":
            table, fields = parts[1].strip(), []
            tables.setdefault(table, [])
        elif tag == "%F":
            fields = parts[1:]
        elif tag == "%R" and table:
            tables[table].append(dict(zip(fields, parts[1:])))
    if not tables.get("TASK"):
        raise ToolError("no TASK table found: this does not look like a P6 XER export")

    projects = tables.get("PROJECT", [])
    counts: dict[str, int] = {}
    for t in tables["TASK"]:
        counts[t.get("proj_id", "")] = counts.get(t.get("proj_id", ""), 0) + 1
    if project:
        chosen = next((p for p in projects if project in (p.get("proj_id"), p.get("proj_short_name"))), None)
        if not chosen:
            raise ToolError(f"no project {project!r} in the file; projects: {', '.join(p.get('proj_short_name', p.get('proj_id', '')) for p in projects)}")
        proj_id = chosen["proj_id"]
    else:
        proj_id = max(counts, key=counts.get)
        chosen = next((p for p in projects if p.get("proj_id") == proj_id), {})

    hours = {c.get("clndr_id"): _dec(c.get("day_hr_cnt"), Decimal(8)) or Decimal(8) for c in tables.get("CALENDAR", [])}
    resourced = {r.get("task_id") for r in tables.get("TASKRSRC", [])}
    status_map = {"TK_Complete": "complete", "TK_Active": "in progress", "TK_NotStart": "not started"}
    type_map = {"TT_Task": "task", "TT_Rsrc": "task", "TT_Mile": "milestone", "TT_FinMile": "milestone", "TT_LOE": "level of effort", "TT_WBS": "summary"}

    acts: dict[str, dict[str, Any]] = {}
    by_task_id: dict[str, str] = {}
    for t in tables["TASK"]:
        if t.get("proj_id") != proj_id:
            continue
        per_day = hours.get(t.get("clndr_id"), Decimal(8))
        key = t.get("task_code") or t.get("task_id")
        if key in acts:
            key = f"{key} [{t.get('task_id')}]"
        by_task_id[t.get("task_id", "")] = key
        float_hr = t.get("total_float_hr_cnt")
        acts[key] = {
            "name": t.get("task_name", ""),
            "type": type_map.get(t.get("task_type", ""), "task"),
            "status": status_map.get(t.get("status_code", ""), "not started"),
            "original_duration": _dec(t.get("target_drtn_hr_cnt")) / per_day,
            "remaining_duration": _dec(t.get("remain_drtn_hr_cnt")) / per_day,
            "file_total_float": (_dec(float_hr) / per_day) if float_hr not in (None, "") else None,
            "constraint": P6_HARD.get(t.get("cstr_type", "")) or P6_HARD.get(t.get("cstr_type2", "")),
            "actual_start": _date(t.get("act_start_date")),
            "actual_finish": _date(t.get("act_end_date")),
            "forecast_start": _date(t.get("early_start_date")) or _date(t.get("restart_date")),
            "forecast_finish": _date(t.get("early_end_date")) or _date(t.get("reend_date")),
            "baseline_finish": None,
            "has_resource": t.get("task_id") in resourced,
            "preds": [],
        }
    task_calendar = {t.get("task_id", ""): t.get("clndr_id") for t in tables["TASK"]}
    external = 0
    for r in tables.get("TASKPRED", []):
        succ, pred = by_task_id.get(r.get("task_id", "")), by_task_id.get(r.get("pred_task_id", ""))
        if not succ or not pred:
            external += int(bool(succ or pred))
            continue
        kind = (r.get("pred_type") or "PR_FS").replace("PR_", "")
        per_day = hours.get(task_calendar.get(r.get("task_id", "")), Decimal(8))  # P6 converts lag on the successor's calendar
        acts[succ]["preds"].append((pred, kind if kind in RELATIONSHIPS else "FS", _dec(r.get("lag_hr_cnt")) / per_day))
    return {
        "format": "Primavera P6 XER",
        "name": chosen.get("proj_short_name") or proj_id,
        "data_date": _date(chosen.get("last_recalc_date")) or _date(chosen.get("next_data_date")),
        "deadline": _date(chosen.get("plan_end_date")),
        "projects_in_file": len(counts),
        "external_links_ignored": external,
        "resource_loaded": bool(resourced),
        "has_baseline": False,
        "activities": acts,
    }


def _iso_hours(text: str | None) -> Decimal:
    """MSPDI durations look like PT40H0M0S (or P2DT4H0M0S)."""
    m = re.fullmatch(r"-?P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?", (text or "").strip())
    if not m:
        return ZERO
    d, h, mi, s = (Decimal(x) if x else ZERO for x in m.groups())
    return d * 24 + h + mi / 60 + s / 3600


def parse_mspdi(text: str) -> dict[str, Any]:
    """Read a Microsoft Project XML (MSPDI) file."""
    import xml.etree.ElementTree as ET

    head = text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in text.upper():
        raise ToolError("XML with a DOCTYPE or entities is refused; export plain Microsoft Project XML")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ToolError(f"could not read the XML: {exc}") from None
    ns = {"p": root.tag[1:].split("}")[0]} if root.tag.startswith("{") else {}
    q = (lambda tag: f"p:{tag}") if ns else (lambda tag: tag)

    def get(el: Any, tag: str) -> str | None:
        found = el.find(q(tag), ns)
        return found.text if found is not None else None

    minutes_per_day = _dec(get(root, "MinutesPerDay"), Decimal(480)) or Decimal(480)
    hours_per_day = minutes_per_day / 60
    tasks = root.findall(f"{q('Tasks')}/{q('Task')}", ns)
    if not tasks:
        raise ToolError("no Tasks found: this does not look like Microsoft Project XML")
    assigned = {get(a, "TaskUID") for a in root.findall(f"{q('Assignments')}/{q('Assignment')}", ns) if get(a, "ResourceUID") not in (None, "-65535")}

    acts: dict[str, dict[str, Any]] = {}
    uid_key: dict[str, str] = {}
    links: list[tuple[str, Any]] = []
    has_baseline = False
    for t in tasks:
        uid = get(t, "UID") or ""
        if uid == "0" or get(t, "IsNull") == "1" or get(t, "Active") == "0":
            continue
        key = get(t, "ID") or uid
        uid_key[uid] = key
        pct = _dec(get(t, "PercentComplete"))
        baseline = next((b for b in t.findall(q("Baseline"), ns) if get(b, "Number") in ("0", None)), None)
        if baseline is not None and get(baseline, "Finish"):
            has_baseline = True
        slack = get(t, "TotalSlack")
        acts[key] = {
            "name": get(t, "Name") or "",
            "type": "summary" if get(t, "Summary") == "1" else ("milestone" if get(t, "Milestone") == "1" else "task"),
            "status": "complete" if pct >= 100 else ("in progress" if get(t, "ActualStart") else "not started"),
            "original_duration": _iso_hours(get(t, "Duration")) / hours_per_day,
            "remaining_duration": _iso_hours(get(t, "RemainingDuration") or get(t, "Duration")) / hours_per_day,
            # TotalSlack is stored in tenths of a minute
            "file_total_float": (_dec(slack) / 10 / minutes_per_day) if slack not in (None, "") else None,
            "constraint": MSP_HARD.get(get(t, "ConstraintType") or ""),
            "actual_start": _date(get(t, "ActualStart")),
            "actual_finish": _date(get(t, "ActualFinish")),
            "forecast_start": _date(get(t, "Start")),
            "forecast_finish": _date(get(t, "Finish")),
            "baseline_finish": _date(get(baseline, "Finish")) if baseline is not None else None,
            "has_resource": uid in assigned,
            "preds": [],
        }
        for link in t.findall(q("PredecessorLink"), ns):
            links.append((key, link))
    external = 0
    for key, link in links:
        pred = uid_key.get(get(link, "PredecessorUID") or "")
        if not pred or get(link, "CrossProject") == "1":
            external += 1
            continue
        lag = _dec(get(link, "LinkLag")) / 10 / minutes_per_day  # tenths of a minute
        acts[key]["preds"].append((pred, MSP_LINK.get(get(link, "Type") or "1", "FS"), lag))
    status_date = _date(get(root, "StatusDate")) or _date(get(root, "CurrentDate"))
    return {
        "format": "Microsoft Project XML",
        "name": get(root, "Title") or get(root, "Name") or "project",
        "data_date": status_date,
        "deadline": _date(get(root, "FinishDate")) if get(root, "ScheduleFromStart") != "0" else None,
        "projects_in_file": 1,
        "external_links_ignored": external,
        "resource_loaded": bool(assigned),
        "has_baseline": has_baseline,
        "activities": acts,
    }


# --------------------------------------------------------------------------- DCMA 14-point assessment

def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _check(number: int, name: str, offenders: Iterable[str], base: int, threshold: str, passed: bool | None, explanation: str, unit: str = "activities") -> dict[str, Any]:
    offenders = list(offenders)
    return {
        "check": number,
        "name": name,
        "count": len(offenders),
        "of": base,
        "of_what": unit,
        "percent": _pct(len(offenders), base),
        "threshold": threshold,
        "result": "not assessed" if passed is None else ("pass" if passed else "fail"),
        "examples": offenders[:15],
        "explanation": explanation,
    }


def assess(project: dict[str, Any], contract_finish: date | None = None, near_days: Decimal = Decimal(10)) -> dict[str, Any]:
    acts = project["activities"]
    data_date: date | None = project["data_date"]
    # the activities DCMA looks at: incomplete, not summaries, not level of effort
    live = {k: a for k, a in acts.items() if a["status"] != "complete" and a["type"] not in ("summary", "level of effort")}
    if not live:
        raise ToolError("every activity is complete or a summary: nothing left to assess")
    tasks = {k: a for k, a in live.items() if a["type"] == "task"}

    # the remaining network: completed work is done, so links from it are already satisfied
    net = {k: {"duration": max(a["remaining_duration"], ZERO), "preds": [(p, kind, lag) for p, kind, lag in a["preds"] if p in live]} for k, a in live.items()}
    for k, a in live.items():
        if a["status"] == "in progress":
            # already started: what is left of it can't wait on start-driving links
            net[k]["preds"] = [(p, kind, lag) for p, kind, lag in net[k]["preds"] if kind in ("FF", "SF")]
    cpm = run_cpm(net)
    all_links = [(k, p, kind, lag) for k, a in live.items() for p, kind, lag in a["preds"]]
    succ_count: dict[str, int] = {k: 0 for k in acts}
    for k, a in acts.items():
        for p, _, _ in a["preds"]:
            succ_count[p] = succ_count.get(p, 0) + 1

    def tf(k: str) -> Decimal:
        f = acts[k]["file_total_float"]
        return f if f is not None else cpm["total_float"][k]

    float_source = "the file (as calculated by the scheduling software)" if all(acts[k]["file_total_float"] is not None for k in live) else "this server's logic-only pass"
    n_links = len(all_links)
    checks = [
        _check(1, "Logic", [k for k, a in live.items() if not a["preds"] or not succ_count.get(k)], len(live), "5% or less", None,
               "Incomplete activities missing a predecessor or a successor. Every activity except the first and last should have both."),
        _check(2, "Leads", [f"{p} -> {k}" for k, p, _, lag in all_links if lag < 0], n_links, "0", None,
               "Links with a negative lag. Leads let a successor start before its predecessor and hide the real logic.", "links"),
        _check(3, "Lags", [f"{p} -> {k}" for k, p, _, lag in all_links if lag > 0], n_links, "5% or less", None,
               "Links with a positive lag. Lags stand in for work that should be its own activity.", "links"),
        _check(4, "Relationship types", [f"{p} -{kind}-> {k}" for k, p, kind, _ in all_links if kind != "FS"], n_links, "90% or more finish-to-start", None,
               "Links that are not finish-to-start. Too many SS, FF or SF links make the critical path hard to follow.", "links"),
        _check(5, "Hard constraints", [f"{k} ({a['constraint']})" for k, a in live.items() if a["constraint"]], len(live), "5% or less", None,
               "Constraints that stop logic from moving a date (must start or finish on, start or finish no later than). They can hide delay."),
        _check(6, "High float", [k for k in live if tf(k) > HIGH_DAYS], len(live), "5% or less", None,
               f"Total float over 44 working days, usually a sign of missing logic. Float from {float_source}."),
        _check(7, "Negative float", [k for k in live if tf(k) < 0], len(live), "0", None,
               f"Negative total float: the schedule cannot meet a constraint or deadline. Float from {float_source}."),
        _check(8, "High duration", [k for k, a in tasks.items() if a["original_duration"] > HIGH_DAYS], len(tasks), "5% or less", None,
               "Tasks over 44 working days. Long tasks are hard to status and should be broken up.", "tasks"),
    ]
    for c, limit in zip(checks, ("5", "0", "5", None, "5", "5", "0", "5")):
        if c["check"] == 4:
            c["result"] = "pass" if _pct(n_links - c["count"], n_links) >= 90 or not n_links else "fail"
            c["percent_finish_to_start"] = _pct(n_links - c["count"], n_links)
        else:
            c["result"] = "pass" if c["percent"] <= float(limit) and (limit != "0" or c["count"] == 0) else "fail"

    if data_date:
        bad = [k for k, a in acts.items() if (a["actual_start"] and a["actual_start"] > data_date) or (a["actual_finish"] and a["actual_finish"] > data_date)]
        bad += [k for k, a in live.items() if a["status"] == "not started" and a["forecast_start"] and a["forecast_start"] < data_date]
        bad += [k for k, a in live.items() if a["forecast_finish"] and a["forecast_finish"] < data_date]
        checks.append(_check(9, "Invalid dates", sorted(set(bad)), len(acts), "0", not bad,
                             f"Actual dates after the data date ({data_date}), or forecast dates before it. Either means the schedule was not statused properly."))
    else:
        checks.append(_check(9, "Invalid dates", [], len(acts), "0", None, "Needs a data date (status date), which the file does not give."))

    if project["resource_loaded"]:
        missing = [k for k, a in tasks.items() if a["original_duration"] > 0 and not a["has_resource"]]
        checks.append(_check(10, "Resources", missing, len(tasks), "0", not missing, "Tasks with duration but no resource or cost assignment.", "tasks"))
    else:
        checks.append(_check(10, "Resources", [], len(tasks), "0", None, "The schedule is not resource-loaded, so this check does not apply.", "tasks"))

    if project["has_baseline"] and data_date:
        due = {k: a for k, a in acts.items() if a["type"] not in ("summary", "level of effort") and a["baseline_finish"] and a["baseline_finish"] <= data_date}
        missed = [k for k, a in due.items() if not a["actual_finish"] or a["actual_finish"] > a["baseline_finish"]]
        checks.append(_check(11, "Missed tasks", missed, len(due), "5% or less", _pct(len(missed), len(due)) <= 5,
                             "Activities due by the data date under the baseline that finished late or have not finished."))
        done = sum(1 for a in due.values() if a["actual_finish"])
        bei = round(done / len(due), 2) if due else None
        c = _check(14, "Baseline Execution Index", [], len(due), "0.95 or more", None if bei is None else bei >= 0.95,
                   "Activities finished divided by activities that should have finished by the data date under the baseline.")
        c["value"] = bei
        bei_check = c
    else:
        checks.append(_check(11, "Missed tasks", [], 0, "5% or less", None, "Needs a baseline and a data date."))
        bei_check = _check(14, "Baseline Execution Index", [], 0, "0.95 or more", None, "Needs a baseline and a data date.")

    # critical path test: add a big delay to the first driving activity and see the finish move by the same amount
    chain = cpm["chain"]
    if chain:
        bumped = {k: dict(v) for k, v in net.items()}
        bumped[chain[0]]["duration"] = bumped[chain[0]]["duration"] + 600
        moved = run_cpm(bumped)["finish"] - cpm["finish"]
        cp = _check(12, "Critical path test", [] if moved == 600 else [chain[0]], 1, "finish moves by the full delay", moved == 600,
                    "A 600-day delay was added to the first activity on the driving path; the project finish should move by exactly that much. "
                    "This server applies logic only; repeat the test in your scheduling software, where constraints and calendars apply.", "tests")
    else:
        cp = _check(12, "Critical path test", [], 1, "finish moves by the full delay", False, "No continuous driving path was found.", "tests")
    checks.append(cp)

    remaining = cpm["finish"]
    if contract_finish is None:
        contract_finish = project.get("deadline")
    if contract_finish and data_date and remaining > 0:
        available = Decimal(workdays_between(data_date, contract_finish))
        cpli = round(float(available / remaining), 2)
        c = _check(13, "Critical Path Length Index", [], 1, "0.95 or more", cpli >= 0.95,
                   f"Working days to the contract finish ({contract_finish}) divided by the remaining critical path length ({float(remaining):g} working days, logic only). Below 1.0 the finish is at risk; below 0.95 it is unlikely without recovery.", "index")
        c["value"] = cpli
    else:
        c = _check(13, "Critical Path Length Index", [], 1, "0.95 or more", None, "Needs a contract finish date (pass contract_finish) and a data date.", "index")
    checks.append(c)
    checks.append(bei_check)
    checks.sort(key=lambda c: c["check"])

    assessed = [c for c in checks if c["result"] != "not assessed"]
    by_float = sorted(live, key=lambda k: (cpm["total_float"][k], cpm["es"][k]))
    start = data_date
    finish_date = workday(start, max(int(remaining.to_integral_value(rounding="ROUND_CEILING")) - 1, 0)).isoformat() if start else None
    file_finish = max((a["forecast_finish"] for a in live.values() if a["forecast_finish"]), default=None)

    def label(k: str) -> dict[str, Any]:
        a = acts[k]
        return {"id": k, "name": a["name"], "remaining_days": _num(net[k]["duration"]), "total_float_days": _num(tf(k)), "status": a["status"]}

    return {
        "project": {
            "name": project["name"],
            "format": project["format"],
            "data_date": data_date.isoformat() if data_date else None,
            "activities": len(acts),
            "complete": sum(1 for a in acts.values() if a["status"] == "complete"),
            "in_progress": sum(1 for a in acts.values() if a["status"] == "in progress"),
            "not_started": sum(1 for a in acts.values() if a["status"] == "not started" and a["type"] != "summary"),
            "relationships": sum(len(a["preds"]) for a in acts.values()),
            "external_links_ignored": project["external_links_ignored"],
            "projects_in_file": project["projects_in_file"],
        },
        "forecast": {
            "remaining_duration_days": _num(remaining),
            "finish_logic_only": finish_date,
            "finish_in_file": file_finish.isoformat() if file_finish else None,
            "note": "finish_logic_only runs the remaining logic from the data date on a Monday-to-Friday calendar with no constraints. If it differs much from finish_in_file, calendars or constraints are driving the dates.",
        },
        "driving_path": [label(k) for k in chain[:60]],
        "lowest_float": [label(k) for k in by_float[:15]],
        "dcma_14_point": {
            "passed": sum(1 for c in assessed if c["result"] == "pass"),
            "failed": sum(1 for c in assessed if c["result"] == "fail"),
            "not_assessed": len(checks) - len(assessed),
            "checks": checks,
        },
        "note": (
            "The DCMA 14-point assessment is a screening test of schedule quality, not a verdict: a failed check is a "
            "question to ask the scheduler. Durations and float are in working days."
        ),
    }


def _num(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.01")).normalize()) if v != v.to_integral_value() else float(v)
