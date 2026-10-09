"""Everything the server offers, gathered from the modules that define it."""

from __future__ import annotations

from . import records, tools

TOOLS = tools.TOOLS + records.TOOLS
HANDLERS = {**tools.HANDLERS, **records.HANDLERS}
RESOURCES = tools.RESOURCES + records.RESOURCES
