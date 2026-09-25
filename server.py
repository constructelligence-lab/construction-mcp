#!/usr/bin/env python3
"""Entry point for MCP clients: python3 /path/to/construction-mcp/server.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from construction_mcp.__main__ import main  # noqa: E402

main()
