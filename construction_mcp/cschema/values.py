"""Strict value parsing for bundle records.

Strict on purpose: a money value with a third decimal place usually means a unit price landed in the wrong field,
and a date in 03/04/2026 form is a guess. Each parser raises ValueError with a message a person can act on.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

CENT = Decimal("0.01")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
        if d.is_finite():
            return d
    raise ValueError(f"not a number: {value!r}")


def money(value: Any) -> Decimal:
    d = _decimal(value)
    if d != d.quantize(CENT):
        raise ValueError(f"more than two decimal places: {value!r}")
    return d.quantize(CENT)


def number(value: Any) -> Decimal:
    return _decimal(value)


def integer(value: Any) -> int:
    d = _decimal(value)
    if d != d.to_integral_value():
        raise ValueError(f"not a whole number: {value!r}")
    return int(d)


def date(value: Any) -> dt.date:
    if not isinstance(value, str) or not DATE.match(value):
        raise ValueError(f"not a YYYY-MM-DD date: {value!r}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"not a real date: {value!r}") from None


def datetime(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"not an ISO 8601 date-time: {value!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"not an ISO 8601 date-time: {value!r}") from None


def boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"expected true or false, got {value!r}")


def text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected text, got {value!r}")
    return value


def ref(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"expected a record id, got {value!r}")
    return value


def refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"expected a list of record ids, got {value!r}")
    return [ref(v) for v in value]


PARSERS = {
    "text": text,
    "enum": text,
    "money": money,
    "number": number,
    "int": integer,
    "date": date,
    "datetime": datetime,
    "bool": boolean,
    "ref": ref,
    "refs": refs,
}


def empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []
