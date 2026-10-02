"""Prompts: reusable construction workflows a client can offer as slash commands.

Each prompt turns a few arguments into one user message that tells the model what to produce and which of this
server's tools to use for the arithmetic, so the numbers come from the calculators and not from the model.
"""

from __future__ import annotations

from typing import Any

from .protocol import ToolError

CHECK_NOTE = "Say plainly which figures are planning values and what the user must verify against their own documents."


def _arg(name: str, description: str, required: bool = True) -> dict[str, Any]:
    return {"name": name, "description": description, "required": required}


def _optional(args: dict[str, str], key: str, label: str) -> str:
    value = (args.get(key) or "").strip()
    return f"\n{label}: {value}" if value else ""


def draft_rfi(args: dict[str, str]) -> str:
    return (
        "Draft a request for information (RFI) to the design team.\n\n"
        f"The question or conflict: {args['issue']}"
        + _optional(args, "references", "Drawing, detail or spec references")
        + _optional(args, "needed_by", "Answer needed by")
        + "\n\nWrite it with these parts: a short subject line; the question, one issue only, stated so it can be answered "
        "yes, no or with a dimension; the references; what was found in the field or documents; a proposed solution, "
        "if there is a sensible one, marked as a suggestion; the possible cost and schedule impact (say 'to be "
        "determined' rather than guess); and the date the answer is needed, with the activity it holds up. Use "
        "define_term for any term you are unsure of. Do not assign fault and do not state that a change is "
        "approved. Keep it under 250 words."
    )


def check_pay_application(args: dict[str, str]) -> str:
    return (
        "Check this progress payment application.\n\n"
        f"Schedule of values and amounts: {args['lines']}"
        + _optional(args, "retainage", "Retainage terms")
        + _optional(args, "previous_certificates", "Total certified on earlier applications")
        + "\n\nPut the lines into the pay_application tool rather than adding them up yourself. Then report: the "
        "current payment due; any line billed over its scheduled value; lines whose percent complete looks ahead of "
        "the work you were told about (front-loading); stored materials that need backup such as invoices, insurance "
        "and a bill of sale; and whether the retainage matches the stated terms. End with a short list of questions "
        "to send back before approving. " + CHECK_NOTE
    )


def review_change_order(args: dict[str, str]) -> str:
    return (
        "Review this change order proposal.\n\n"
        f"Scope of the change: {args['scope']}\n"
        f"Proposed costs and markups: {args['pricing']}"
        + _optional(args, "contract_terms", "The contract's markup and changes terms")
        + "\n\nRecompute the total with the change_order_markup tool using the stated costs and markups, and say "
        "whether it matches the proposal. Check the markups against the contract terms if given, including the order "
        "they are applied in and any lower rate on subcontract work. List what is missing or unsupported: quantities, "
        "labor hours and rates, material quotes, equipment rates, time extension, and costs that look duplicated "
        "between lines. Use lookup_cost_code to suggest a cost code. Do not decide entitlement; flag questions "
        "for the project team. " + CHECK_NOTE
    )


def schedule_review(args: dict[str, str]) -> str:
    return (
        "Review this schedule logic.\n\n"
        f"Activities, durations and predecessors: {args['activities']}"
        + _optional(args, "start_date", "Start date")
        + "\n\nRun it through the critical_path tool rather than working the dates out yourself. Then explain: the "
        "project duration and finish; the driving critical path in plain words; near-critical activities and how "
        "much float they have; open ends, loops or leads the tool warns about; and sequencing that looks wrong for "
        "the trades involved (use trade_sequence to compare with a typical sequence). Suggest at most three logic "
        "fixes, each with why. " + CHECK_NOTE
    )


def quantity_takeoff(args: dict[str, str]) -> str:
    return (
        "Make a quick quantity takeoff from this description.\n\n"
        f"Scope and dimensions: {args['scope']}"
        + "\n\nBreak it into elements, list the dimensions you are using for each, and compute the quantities with "
        "this server's calculators (concrete_volume, rebar_weight, masonry_units, asphalt_tonnage, earthwork_volume, "
        "convert_units) instead of doing the arithmetic yourself. Show neat quantities and quantities with waste "
        "(waste_factor), and the pay unit for each item (pay_unit). Ask for any dimension you would otherwise have "
        "to assume, and mark any assumption you do make. Present the result as a table. " + CHECK_NOTE
    )


PROMPTS: list[dict[str, Any]] = [
    {
        "name": "draft_rfi",
        "title": "Draft an RFI",
        "description": "Turn a field question or document conflict into a clear, single-issue request for information.",
        "arguments": [
            _arg("issue", "The question or conflict, in your own words"),
            _arg("references", "Drawing, detail or spec section references", False),
            _arg("needed_by", "When the answer is needed, and what it holds up", False),
        ],
        "render": draft_rfi,
    },
    {
        "name": "check_pay_application",
        "title": "Check a pay application",
        "description": "Check a progress payment: totals, retainage, over-billed lines, front-loading and backup for stored materials.",
        "arguments": [
            _arg("lines", "Schedule of values lines: scheduled value, previous, this period and stored, per line"),
            _arg("retainage", "Retainage terms, e.g. '10% on work, 5% on stored materials'", False),
            _arg("previous_certificates", "Total certified on earlier applications", False),
        ],
        "render": check_pay_application,
    },
    {
        "name": "review_change_order",
        "title": "Review a change order",
        "description": "Recompute a change order's markups, check them against the contract, and list what is missing.",
        "arguments": [
            _arg("scope", "What the change is"),
            _arg("pricing", "The proposed direct costs and markups"),
            _arg("contract_terms", "The contract's markup and changes clause, if you have it", False),
        ],
        "render": review_change_order,
    },
    {
        "name": "schedule_review",
        "title": "Review schedule logic",
        "description": "Find the critical path, near-critical work and logic problems in a list of activities.",
        "arguments": [
            _arg("activities", "Activities with durations and predecessors, as a list or table"),
            _arg("start_date", "Start date, e.g. 2026-03-02", False),
        ],
        "render": schedule_review,
    },
    {
        "name": "quantity_takeoff",
        "title": "Quick quantity takeoff",
        "description": "Turn a described scope into quantities with waste and pay units, using the calculators.",
        "arguments": [_arg("scope", "The scope and its dimensions, e.g. 'a 40 x 60 slab, 6 inches, with #4 at 12 inches each way'")],
        "render": quantity_takeoff,
    },
]

def get_prompt(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    prompt = next((p for p in PROMPTS if p["name"] == name), None)
    if prompt is None:
        raise KeyError(name)
    if not isinstance(arguments, dict) or any(not isinstance(v, str) for v in arguments.values()):
        raise ToolError("prompt arguments must be an object of strings")
    missing = [a["name"] for a in prompt["arguments"] if a["required"] and not (arguments.get(a["name"]) or "").strip()]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")
    text = prompt["render"](arguments)
    return {"description": prompt["description"], "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
