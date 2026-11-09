# construction-mcp: construction MCP server for Claude, Cursor and AI assistants

**construction-mcp** is a free, open source (MIT) construction MCP server: a small, offline [Model Context Protocol](https://modelcontextprotocol.io) server for Claude Desktop, Claude Code, Cursor, VS Code and any MCP client. It gives an AI assistant
construction reference data and calculators it would otherwise guess at: cost codes, construction terms, waste
factors, pay units, the typical trade sequence, cost and schedule metrics, unit conversions, and calculators for
concrete, rebar, masonry, asphalt, earthwork, earned value, retainage and markup versus margin. It also runs the
arithmetic models most often get wrong: a **critical path** pass over your activities, **pay application** totals
and **change order** markups. And it reads project records in the open
[construction schema](https://github.com/constructelligence-lab/construction-schema): it validates them and follows
the links from an RFI to the change order and the costs behind it.

It is one Python file tree with **no dependencies, no outbound network access and no accounts**. The data ships
with it. Run it locally over stdio, or [host it at a URL](#host-it-online) for a team or for claude.ai.

![tools](https://img.shields.io/badge/tools-21-1f6feb) ![prompts](https://img.shields.io/badge/prompts-5-1f6feb) ![datasets](https://img.shields.io/badge/datasets-8-1f6feb) ![dependencies](https://img.shields.io/badge/dependencies-none-2ea043) ![outbound network](https://img.shields.io/badge/outbound%20network-none-2ea043) ![transports](https://img.shields.io/badge/transports-stdio%20%2B%20HTTP-1f6feb) ![licence](https://img.shields.io/badge/licence-MIT%20%2B%20CC%20BY%204.0-6e7781)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/tools-dark.svg">
    <img src="assets/tools-light.svg" alt="construction-mcp has 21 tools in four groups: reference lookups, calculators, schedule and money, and project records" width="100%">
  </picture>
</p>

## Tools

**Reference lookups**

| Tool | Ask it things like | Returns |
| --- | --- | --- |
| `lookup_cost_code` | "What cost code for slab on grade?" "List division 09" | Code, description, CSI division, cost type, unit, notes |
| `define_term` | "What is retainage?" "What does AHJ mean?" | Glossary definitions from contract, cost control, field, safety and finance |
| `waste_factor` | "What waste should I carry on tile?" | Planning low and high percent, basis, and what drives it |
| `pay_unit` | "How is bulk excavation paid?" | Pay unit, measurement basis and usual exclusions |
| `trade_sequence` | "What comes before drywall?" | Trade, activity, predecessor, duration driver and the constraint that usually bites |
| `metric_formula` | "How is TCPI calculated?" | Formula, inputs, interpretation and caveats |
| `convert_units` | "Convert 2 M3 to CY" "450 PSF in KSF" | The result, factor and notes. Refuses conversions that need more information, such as LF to SF |

**Calculators**

| Tool | Ask it things like | Returns |
| --- | --- | --- |
| `concrete_volume` | "40 by 30 slab, 6 inches thick" | Neat CF and CY, and order CY with a given waste or the planning range |
| `rebar_weight` | "100 #5 bars at 20 feet" | Pounds and tons, with the waste range |
| `masonry_units` | "1,000 SF of CMU wall with 80 SF of openings" | Units neat and with waste |
| `asphalt_tonnage` | "9,000 SF paved at 3 inches" | Tons neat and with waste |
| `earthwork_volume` | "1,000 bank yards of clay: how much to haul?" | Loose and compacted volume ranges |
| `earned_value` | "BAC 1M, EV 400k, AC 450k, PV 500k" | CV, SV, CPI, SPI, two EACs, ETC, VAC and TCPI, with warnings when it is too early to trust them |
| `retainage` | "10% retainage on 500k billed, 380k already paid" | Retainage held and outstanding, and the amount due |
| `markup_margin` | "Cost 100,000 at 20% markup: what's my margin?" | Price, profit, markup and margin |

**Schedule and money**

| Tool | Ask it things like | Returns |
| --- | --- | --- |
| `critical_path` | "Here are my 40 activities and their logic: what's critical?" | Duration, the driving path, near-critical work, early and late dates, total and free float, dates on a Monday to Friday calendar, and warnings for loops, open ends and leads. Handles FS, SS, FF and SF links with lags |
| `pay_application` | "Check this pay app: 10% retainage, 5% on stored materials" | Per line: completed and stored, percent, balance and retainage. Summary: earned less retainage, less previous certificates, payment due, balance to finish. Flags lines billed over their scheduled value |
| `change_order_markup` | "Labor 10k with 35% burden, sub 20k at 5%, 10 and 10 OH&P, 1% bond" | The line-by-line build-up in a stated order, the total and the effective markup on direct cost |

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/critical-path-dark.svg">
    <img src="assets/critical-path-light.svg" alt="A six-activity network with the critical path A, B, D, F highlighted at 14 days, and 4 days of float on the other two activities, as returned by the critical_path tool" width="100%">
  </picture>
</p>

**Project records** in the [construction schema](https://github.com/constructelligence-lab/construction-schema)

| Tool | Ask it things like | Returns |
| --- | --- | --- |
| `schema_describe` | "What fields does a change order have?" | The 17 entities, or one entity's fields, allowed values and what links to it |
| `records_validate` | "Check these records before I load them" | Findings with check codes: missing or mistyped fields, duplicate ids, broken or cross-project links, values read from documents or by models without a confidence |
| `records_trace` | "What came of RFI-011?" | The linked records in both directions (spec sections, submittals, change events, change orders, costs, pay applications), a tree, and a Markdown brief with the source of every record |

The records come in with the call; the server keeps nothing. To turn CSV exports into records, use `cschema convert`
in the construction-schema repository.

Every tool is read-only. The reference and calculator tools tell the model their results are planning values to be
checked against your own data.

## Prompts

Five workflows that clients such as Claude Desktop and Claude Code offer as slash commands. Each one tells the
model what to produce and which tools to use for the numbers.

| Prompt | What it does |
| --- | --- |
| `draft_rfi` | Turns a field question or a document conflict into a clear, single-issue RFI with references, impact and a needed-by date |
| `check_pay_application` | Recomputes a pay application and flags over-billed lines, front-loading, stored materials without backup, and retainage that doesn't match the terms |
| `review_change_order` | Recomputes the markups, checks them against the contract terms and lists what is missing or unsupported |
| `schedule_review` | Finds the critical path and near-critical work, and suggests up to three logic fixes |
| `quantity_takeoff` | Turns a described scope into quantities with waste and pay units, as a table |

## Resources

The eight datasets behind the tools are also exposed as read-only MCP resources
(`construction-reference://data/<name>.csv`), so a client can attach a whole table to a conversation: cost codes,
glossary, waste factors, earthwork factors, unit conversions, pay units, trade sequence and metric formulas. So is the
schema (`construction-reference://schema/entities.csv` and `fields.csv`). Only these bundled files can be read.

## What it does not do

- **No outbound network.** It never makes a request to anything. By default it talks only to the application that
  launched it, over standard input and output; in HTTP mode it listens on the port you give it and still calls
  nothing. A test fails the build if network client code appears.
- **No connections.** It does not connect to any accounting, project management or scheduling system, to any
  Constructelligence product or server, or to anything else.
- **No files, no memory.** It reads only its own bundled data. It does not read your files, write anything, or
  keep anything between calls.
- **No advice.** Waste factors, swell factors and unit weights are planning ranges. Your own history, your
  suppliers and your engineers beat any table.

## Install

**Quickest, with [uv](https://docs.astral.sh/uv/):** no clone needed.

```bash
claude mcp add construction-reference -- uvx --from git+https://github.com/constructelligence-lab/construction-mcp construction-mcp
```

**Or clone it.** Python 3.9 or later is all it needs.

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

## Host it online

The same server speaks MCP's Streamable HTTP transport, so you can run it once and let everyone connect by URL:
your team, Claude Code with `--transport http`, or claude.ai as a custom connector.

```bash
python3 server.py --http                      # http://127.0.0.1:8000/mcp, this machine only
python3 server.py --http --host 0.0.0.0 --token "$(openssl rand -hex 24)"
```

Or with Docker, which listens on `$PORT` (default 8000):

```bash
docker build -t construction-mcp .
docker run -p 8000:8000 -e CONSTRUCTION_MCP_TOKEN=your-long-random-token construction-mcp
```

Then connect a client:

```bash
claude mcp add --transport http construction-reference https://your-host.example/mcp \
  --header "Authorization: Bearer your-long-random-token"
```

| Option | Environment variable | Default | What it does |
| --- | --- | --- | --- |
| `--http` | `CONSTRUCTION_MCP_HTTP=1` | off (stdio) | Serve HTTP instead of stdio |
| `--host` | `HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` in a container |
| `--port` | `PORT` | `8000` | Port |
| `--path` | `CONSTRUCTION_MCP_PATH` | `/mcp` | Endpoint path |
| `--token` | `CONSTRUCTION_MCP_TOKEN` | none | Require `Authorization: Bearer <token>` |
| `--allowed-origin` | `CONSTRUCTION_MCP_ALLOWED_ORIGINS` (comma-separated) | none | Browser origins allowed to call it |
| `--rate-limit` | `CONSTRUCTION_MCP_RATE_LIMIT` | `120` | Requests per minute per client IP; `0` turns it off |
| `--trust-proxy` | `CONSTRUCTION_MCP_TRUST_PROXY=1` | off | Rate-limit by `X-Forwarded-For`; only behind a proxy you run |

What the HTTP mode does to stay safe when it is reachable:

- **Local by default.** It binds to `127.0.0.1` unless you choose another address, and warns if you expose it with no token.
- **Origin checks.** Browser requests from an origin you haven't allowed get a 403, which blocks DNS rebinding.
  On a local bind, `localhost` pages are allowed so the MCP Inspector works.
- **Limits.** Bodies over 1 MB get a 413, stalled connections time out after 30 seconds, and each client IP has a
  request budget per minute (429 when spent).
- **Stateless.** No sessions and no server-to-client stream, so `GET /mcp` returns 405. Every request stands alone,
  which makes it easy to run behind a load balancer. `GET /health` is a token-free health check.

Use HTTPS in front of it (your platform's load balancer, Caddy or nginx) for anything beyond your own machine. The
token is a shared secret, not per-user sign-in. claude.ai custom connectors expect either no authentication or
OAuth, so to add it there, run it without a token. The tools are read-only and the data is public, so the rate limit is
then the main protection.

## Check it works

```bash
python3 -m unittest discover -s tests
```

The tests start the real server the way a client does and call every tool. To poke at it by hand:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"markup_margin","arguments":{"cost":100000,"markup_percent":20}}}' | python3 server.py
```

## Protocol

JSON-RPC 2.0, either over stdio (one message per line) or over Streamable HTTP (stateless, one JSON response per
POST, 202 for notifications). Implements `initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`,
`resources/read`, `prompts/list` and `prompts/get`, and accepts MCP protocol versions `2025-11-25`, `2025-06-18`,
`2025-03-26` and `2024-11-05`; over HTTP an unsupported `MCP-Protocol-Version` header gets a 400. Tool results carry
both a JSON text block and `structuredContent`. Input problems come back as tool results with `isError: true`,
so the model can correct its call. Logging goes to stderr.

## Data

The reference data is copied from
[construction-data](https://github.com/constructelligence-lab/construction-data), which is where corrections
belong. See [`construction_mcp/data/SOURCE.md`](construction_mcp/data/SOURCE.md). The cost codes use an
independent numbering mapped to CSI division titles only; they are a starting point, not a standard.

The schema and the code that reads it are copied from
[construction-schema](https://github.com/constructelligence-lab/construction-schema); see
[`construction_mcp/cschema/SOURCE.md`](construction_mcp/cschema/SOURCE.md).

<!-- begin:family -->
## More from Constructelligence

Open construction resources from the same team, all maintained alongside this one:

| Repository | What it is |
| --- | --- |
| [AI in construction](https://github.com/constructelligence-lab/ai-in-construction) | A practical guide to AI in construction: what works today, what the data has to look like, the risks, and a 90-day plan. |
| [Construction data migration](https://github.com/constructelligence-lab/construction-data-migration) | A guide and toolkit for moving a contractor between systems, and proving nothing was lost. |
| [Construction project records](https://github.com/constructelligence-lab/construction-project-records) | Open schemas, templates and a checker for RFIs, submittals, change events, daily reports and punch lists. |
| [Construction schema](https://github.com/constructelligence-lab/construction-schema) | One record format for construction project data, with provenance, classifications and links, built for AI. |
| [Construction reference data](https://github.com/constructelligence-lab/construction-data) | Cost codes, units, waste factors, pay units, trade sequence, glossary and metric formulas in CSV. |
| [Construction prompts](https://github.com/constructelligence-lab/construction-prompts) | 28 prompts for ChatGPT, Claude and Gemini, from bid go/no-go to notice letters. |
| [Construction agent skills](https://github.com/constructelligence-lab/construction-agent-skills) | 28 installable agent skills for Claude Code and any agent that reads SKILL.md. |
| [Open source construction tools](https://github.com/constructelligence-lab/open-source-construction-tools) | Open source software for BIM, CAD, scheduling and site work, verified against the GitHub API. |
| [Revit MCP server](https://github.com/constructelligence-lab/revit-mcp) | Connect Autodesk Revit to Claude, Cursor and any MCP client: read the model, render photoreal pictures with Blender, export PDF/IFC/glTF. Read-only until you allow writes. |

<!-- end:family -->


## Frequently asked questions

**What is it for?** Construction estimating, scheduling and cost control questions where a language model would otherwise guess: CSI cost codes, waste factors, concrete and rebar quantities, critical path method (CPM) scheduling, earned value, retainage, pay application checks and change order markups.

**Does it work with Claude Desktop, Claude Code and Cursor?** Yes. Any MCP client that can run a stdio server, or connect to a URL if you [host it](#host-it-online).

**Does it send my project data anywhere?** No. It has no dependencies and makes no outbound network connections; the data ships with it and nothing is stored.

**Can it check a schedule?** `critical_path` runs a CPM pass on your activities (FS, SS, FF, SF links with lags) and reports the driving path, float, open ends and loops.

**Can it check a pay application?** `pay_application` totals completed and stored work, retainage and payment due per line, and flags lines billed over their scheduled value.

**Does it work with Revit?** Pair it with the [Revit MCP server](https://github.com/constructelligence-lab/revit-mcp): Revit supplies the quantities, this server supplies the factors, codes and calculators.

**Is the data reliable?** It is planning data, labelled as such, with sources. Verify quantities, factors and prices against your own project data before relying on them.

## Licence

Code: **MIT**. Data and documentation: **CC BY 4.0**. See [`LICENSE`](LICENSE).

---

*Maintained by [Constructelligence](https://constructelligence.co) — building the AI infrastructure for
construction.*
