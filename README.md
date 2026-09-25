# Construction reference MCP server

A small, offline [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI assistant
construction reference data and a few calculators it would otherwise guess at: cost codes, construction terms,
waste factors, unit conversions, concrete volumes, earthwork swell and shrink, and markup versus margin.

It is one Python file tree with **no dependencies, no network access and no accounts**. The data ships with it.

![tools](https://img.shields.io/badge/tools-7-1f6feb) ![dependencies](https://img.shields.io/badge/dependencies-none-2ea043) ![network](https://img.shields.io/badge/network-none-2ea043) ![licence](https://img.shields.io/badge/licence-MIT%20%2B%20CC%20BY%204.0-6e7781)

## Tools

| Tool | Ask it things like | Returns |
| --- | --- | --- |
| `lookup_cost_code` | "What cost code for slab on grade?" "List division 09" | Code, description, CSI division, cost type, unit, notes |
| `define_term` | "What is retainage?" "What does AHJ mean?" | Glossary definitions from contract, cost control, field, safety and finance |
| `waste_factor` | "What waste should I carry on tile?" | Planning low and high percent, basis, and what drives it |
| `convert_units` | "Convert 2 M3 to CY" "450 PSF in KSF" | The result, factor and notes. Refuses conversions that need more information, such as LF to SF |
| `concrete_volume` | "40 by 30 slab, 6 inches thick" | Neat CF and CY, and order CY with a given waste or the planning range |
| `earthwork_volume` | "1,000 bank yards of clay: how much to haul?" | Loose and compacted volume ranges |
| `markup_margin` | "Cost 100,000 at 20% markup: what's my margin?" | Price, profit, markup and margin |

Every tool is read-only and tells the model its results are planning values to be checked against your own data.

## What it does not do

- **No network.** It never opens a port or makes a request. It talks only to the application that launched it,
  over standard input and output.
- **No connections.** It does not connect to any accounting, project management or scheduling system, to any
  Constructelligence product or server, or to anything else.
- **No files, no memory.** It reads only its own bundled data. It does not read your files, write anything, or
  keep anything between calls.
- **No advice.** Waste factors, swell factors and unit weights are planning ranges. Your own history, your
  suppliers and your engineers beat any table.

## Install

Clone the repository anywhere. Python 3.9 or later is all it needs.

```bash
git clone https://github.com/constructelligence-lab/construction-mcp.git
```

Then add it to your MCP client. Replace the path with where you cloned it.

**Claude Code**

```bash
claude mcp add construction-reference -- python3 /path/to/construction-mcp/server.py
```

**Claude Desktop and other clients** that take a JSON configuration:

```json
{
  "mcpServers": {
    "construction-reference": {
      "command": "python3",
      "args": ["/path/to/construction-mcp/server.py"]
    }
  }
}
```

Nothing is installed system-wide, and removing it is deleting the folder and that entry.

## Check it works

```bash
python3 -m unittest discover -s tests
```

The tests start the real server the way a client does and call every tool. To poke at it by hand:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"markup_margin","arguments":{"cost":100000,"markup_percent":20}}}' | python3 server.py
```

## Protocol

JSON-RPC 2.0 over stdio, one message per line. Implements `initialize`, `ping`, `tools/list` and `tools/call`,
and accepts MCP protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05`. Tool results carry
both a JSON text block and `structuredContent`. Input problems come back as tool results with `isError: true`,
so the model can correct its call. Logging goes to stderr.

## Data

The reference data is copied from
[construction-data](https://github.com/constructelligence-lab/construction-data), which is where corrections
belong. See [`construction_mcp/data/SOURCE.md`](construction_mcp/data/SOURCE.md). The cost codes use an
independent numbering mapped to CSI division titles only; they are a starting point, not a standard.

## Related

- [construction-data](https://github.com/constructelligence-lab/construction-data): the reference data behind these tools.
- [construction-data-migration](https://github.com/constructelligence-lab/construction-data-migration): a guide and
  toolkit for moving a construction company between systems, and proving nothing was lost.
- [construction-agent-skills](https://github.com/constructelligence-lab/construction-agent-skills): installable
  skills for estimating, scheduling, site and commercial work.

Questions or a tool you would like to see? [Open an issue](https://github.com/constructelligence-lab/construction-mcp/issues).

## Licence

Code: **MIT**. Data and documentation: **CC BY 4.0**. See [`LICENSE`](LICENSE).

---

*Maintained by [Constructelligence](https://constructelligence.co) — building the AI infrastructure for
construction.*
