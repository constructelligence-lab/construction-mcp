"""End-to-end tests: start the server as an MCP client would, over stdio, and talk to it.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from construction_mcp.protocol import SUPPORTED_VERSIONS, Server  # noqa: E402
from construction_mcp.catalog import HANDLERS, TOOLS  # noqa: E402


def session(*messages: dict) -> list[dict]:
    """Run the real server process with these messages and return its replies."""
    text = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run([sys.executable, str(ROOT / "server.py")], input=text, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()]


def call(name: str, **arguments) -> dict:
    return session({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})[0]["result"]


INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}


class Protocol(unittest.TestCase):
    def test_handshake_and_listing(self):
        replies = session(INIT, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(replies), 2, "a notification must not get a reply")
        init = replies[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertEqual(init["serverInfo"]["name"], "construction-reference")
        self.assertEqual(init["capabilities"], {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}})
        names = [t["name"] for t in replies[1]["result"]["tools"]]
        self.assertEqual(names, list(HANDLERS))

    def test_unknown_protocol_version_gets_latest(self):
        init = session(dict(INIT, params={"protocolVersion": "1999-01-01"}))[0]["result"]
        self.assertEqual(init["protocolVersion"], SUPPORTED_VERSIONS[0])

    def test_resources(self):
        replies = session(
            INIT,
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "construction-reference://data/glossary.csv"}},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "file:///etc/passwd"}},
        )
        self.assertIn("resources", replies[0]["result"]["capabilities"])
        uris = [r["uri"] for r in replies[1]["result"]["resources"]]
        self.assertEqual(len(uris), 10)
        self.assertTrue(all(u.startswith(("construction-reference://data/", "construction-reference://schema/")) for u in uris))
        content = replies[2]["result"]["contents"][0]
        self.assertEqual(content["mimeType"], "text/csv")
        self.assertTrue(content["text"].startswith("term,category,definition"))
        self.assertEqual(replies[3]["error"]["code"], -32002, "only the bundled datasets can be read")

    def test_errors(self):
        replies = session(
            {"jsonrpc": "2.0", "id": 1, "method": "sampling/createMessage"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "no_such_tool", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        self.assertEqual(replies[0]["error"]["code"], -32601)
        self.assertEqual(replies[1]["error"]["code"], -32602)
        self.assertEqual(replies[2]["result"], {})

    def test_bad_json_does_not_kill_the_server(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "server.py")],
            input="{not json\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_every_tool_is_read_only_and_closed_world(self):
        for tool in TOOLS:
            self.assertTrue(tool["annotations"]["readOnlyHint"], tool["name"])
            self.assertFalse(tool["annotations"]["openWorldHint"], tool["name"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"], tool["name"])

    def test_no_outbound_network_code(self):
        files = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "construction_mcp").glob("*.py")}
        source = "".join(files.values())
        # it may listen (HTTP mode) but never calls out
        for module in ("socket", "urllib.request", "http.client", "requests", "httpx", "aiohttp", "urllib3", "ssl", "subprocess", "ftplib", "smtplib"):
            self.assertNotIn(f"import {module}", source)
            self.assertNotIn(f"from {module} ", source)
        self.assertEqual([n for n, text in files.items() if "http.server" in text], ["http_transport.py"], "only the HTTP transport may listen")


class Tools(unittest.TestCase):
    def test_cost_code_lookup(self):
        r = call("lookup_cost_code", query="slab on grade")["structuredContent"]
        self.assertIn("03-100", [m["cost_code"] for m in r["matches"]])
        r = call("lookup_cost_code", division="9", limit=3)["structuredContent"]
        self.assertEqual(len(r["matches"]), 3)
        self.assertTrue(all(m["csi_division"] == "09" for m in r["matches"]))

    def test_define_term(self):
        r = call("define_term", term="rfi")["structuredContent"]
        self.assertTrue(r["matches"][0]["term"].startswith("RFI"))
        self.assertTrue(call("define_term", term="zzzz")["isError"])

    def test_waste_factor(self):
        r = call("waste_factor", material="drywall gypsum")
        self.assertTrue(r["isError"], "all query words must match")
        r = call("waste_factor", material="gypsum")["structuredContent"]["matches"][0]
        self.assertEqual((r["planning_low_percent"], r["planning_high_percent"]), (10.0, 15.0))

    def test_convert_units(self):
        r = call("convert_units", value=27, from_unit="cf", to_unit="cy")["structuredContent"]
        self.assertEqual(r["result"], 1.0, "divides by the exact 27 rather than multiplying by a rounded 1/27")
        r = call("convert_units", value=2, from_unit="M3", to_unit="CY")["structuredContent"]
        self.assertAlmostEqual(r["result"], 2.6159, places=3)
        self.assertEqual(r["method"], "reciprocal of the listed factor")
        refused = call("convert_units", value=10, from_unit="LF", to_unit="SF")
        self.assertTrue(refused["isError"])
        self.assertIn("width", refused["content"][0]["text"])
        self.assertTrue(call("convert_units", value=1, from_unit="REBAR_4", to_unit="REBAR_LB_FT")["isError"])

    def test_concrete_volume(self):
        r = call("concrete_volume", length_ft=40, width_ft=30, depth_in=6)["structuredContent"]
        self.assertEqual(r["neat_volume_cf"], 600.0)
        self.assertEqual(r["neat_volume_cy"], 22.22)
        self.assertEqual(r["order_volume_cy_range"], [22.89, 24.0])
        r = call("concrete_volume", length_ft=10, width_ft=2, depth_in=12, count=4, waste_percent=5)["structuredContent"]
        self.assertEqual((r["neat_volume_cf"], r["order_volume_cy"]), (80.0, 3.11))
        self.assertTrue(call("concrete_volume", length_ft=0, width_ft=2, depth_in=12)["isError"])

    def test_earthwork_volume(self):
        r = call("earthwork_volume", bank_cy=1000, material="clay")["structuredContent"]
        self.assertEqual(r["loose_cy_range"], [1200.0, 1300.0])
        self.assertEqual(r["compacted_cy_range"], [800.0, 900.0])

    def test_markup_margin(self):
        r = call("markup_margin", cost=100000, markup_percent=20)["structuredContent"]
        self.assertEqual((r["price"], r["margin_percent"]), (120000.0, 16.67))
        r = call("markup_margin", cost=100000, margin_percent=20)["structuredContent"]
        self.assertEqual((r["price"], r["markup_percent"]), (125000.0, 25.0))
        self.assertTrue(call("markup_margin", cost=100, markup_percent=10, margin_percent=10)["isError"])
        self.assertTrue(call("markup_margin", cost=100, margin_percent=100)["isError"])

    def test_bad_arguments_come_back_as_tool_errors(self):
        r = call("concrete_volume", length_ft="forty", width_ft=2, depth_in=6)
        self.assertTrue(r["isError"])
        self.assertIn("length_ft", r["content"][0]["text"])

    def test_pay_unit_and_sequence_and_metrics(self):
        r = call("pay_unit", scope="bulk excavation")["structuredContent"]["matches"][0]
        self.assertEqual((r["pay_unit"], r["measure_basis"]), ("CY", "bank volume"))
        self.assertEqual(len(call("trade_sequence")["structuredContent"]["steps"]), 30)
        self.assertEqual(call("metric_formula", metric="TCPI")["structuredContent"]["matches"][0]["formula"], "(BAC - EV) / (BAC - AC)")

    def test_earned_value(self):
        r = call("earned_value", bac=1000000, ev=400000, ac=450000, pv=500000)["structuredContent"]
        self.assertEqual((r["cost_variance"], r["cpi"], r["spi"]), (-50000.0, 0.889, 0.8))
        self.assertEqual((r["eac_if_performance_continues"], r["eac_if_variance_was_one_off"]), (1125000.0, 1050000.0))
        self.assertEqual(r["tcpi_to_finish_on_budget"], 1.091)
        early = call("earned_value", bac=1000000, ev=50000, ac=60000)["structuredContent"]
        self.assertTrue(any("unreliable" in w for w in early["warnings"]))
        self.assertTrue(call("earned_value", bac=100, ev=200, ac=50)["isError"])

    def test_retainage(self):
        r = call("retainage", work_billed_to_date=500000, retainage_percent=10, previously_paid=380000)["structuredContent"]
        self.assertEqual((r["retainage_outstanding"], r["due_this_application"]), (50000.0, 70000.0))
        self.assertTrue(call("retainage", work_billed_to_date=1000, retainage_percent=10, released=500)["isError"])

    def test_material_calculators(self):
        r = call("rebar_weight", bar_size=5, length_ft=20, count=100)["structuredContent"]
        self.assertEqual((r["weight_lb"], r["weight_tons"]), (2086.0, 1.043))
        self.assertTrue(call("rebar_weight", bar_size=12, length_ft=20)["isError"])
        r = call("masonry_units", wall_area_sf=1000, unit="cmu", openings_sf=80)["structuredContent"]
        self.assertEqual((r["units_neat"], r["units_with_waste_range"]), (1035, [1087, 1139]))
        self.assertEqual(call("masonry_units", wall_area_sf=100, unit="brick")["structuredContent"]["units_neat"], 675)
        r = call("asphalt_tonnage", area_sf=9000, thickness_in=3)["structuredContent"]
        self.assertEqual((r["area_sy"], r["tons_neat"]), (1000.0, 165.0))

    def test_critical_path(self):
        # a textbook network worked by hand: A-B-D-F is critical at 14 days; C and E share 4 days of float
        activities = [
            {"id": "A", "duration": 3},
            {"id": "B", "duration": 4, "predecessors": ["A"]},
            {"id": "C", "duration": 2, "predecessors": ["A"]},
            {"id": "D", "duration": 5, "predecessors": ["B"]},
            {"id": "E", "duration": 3, "predecessors": ["C"]},
            {"id": "F", "duration": 2, "predecessors": ["D", "E"]},
        ]
        r = call("critical_path", activities=activities, start_date="2026-10-05")["structuredContent"]
        self.assertEqual((r["project_duration"], r["critical_path"], r["near_critical"]), (14, ["A", "B", "D", "F"], ["C", "E"]))
        by_id = {a["id"]: a for a in r["activities"]}
        self.assertEqual((by_id["C"]["total_float"], by_id["C"]["free_float"]), (4, 0))
        self.assertEqual((by_id["E"]["total_float"], by_id["E"]["free_float"]), (4, 4))
        # day 0 is Monday 5 October; 14 working days end on Thursday 22 October, skipping the weekend
        self.assertEqual((by_id["A"]["finish_date"], by_id["F"]["start_date"], r["finish_date"]), ("2026-10-07", "2026-10-21", "2026-10-22"))

        # start-to-start and finish-to-finish lags
        r = call("critical_path", activities=[
            {"id": "Frame", "duration": 10},
            {"id": "Rough", "duration": 8, "predecessors": [{"id": "Frame", "type": "SS", "lag": 3}]},
            {"id": "Board", "duration": 6, "predecessors": [{"id": "Rough", "type": "FF", "lag": 2}]},
        ])["structuredContent"]
        self.assertEqual((r["project_duration"], r["critical_path"]), (13, ["Frame", "Rough", "Board"]))
        self.assertEqual([a["early_start"] for a in r["activities"]], [0, 3, 7])
        self.assertTrue(any("no successor depending on their finish (Frame, Board)" in w for w in r["warnings"]), "Frame only drives a start: its finish dangles")

        loop = call("critical_path", activities=[{"id": "A", "duration": 1, "predecessors": ["B"]}, {"id": "B", "duration": 1, "predecessors": ["A"]}])
        self.assertTrue(loop["isError"])
        self.assertIn("loop", loop["content"][0]["text"])
        self.assertTrue(call("critical_path", activities=[{"id": "A", "duration": 1, "predecessors": ["Z"]}])["isError"])
        self.assertTrue(call("critical_path", activities=[{"id": "A", "duration": 1.5}], start_date="2026-10-05")["isError"])

    def test_pay_application(self):
        lines = [
            {"description": "Concrete", "scheduled_value": 100000, "previous_completed": 60000, "this_period": 20000},
            {"description": "Steel", "scheduled_value": 80000, "previous_completed": 40000, "this_period": 10000, "stored_materials": 15000},
            {"description": "Doors", "scheduled_value": 10000, "previous_completed": 9000, "this_period": 2000},
        ]
        r = call("pay_application", lines=lines, retainage_percent=10, stored_materials_retainage_percent=5, previous_certificates=90000)["structuredContent"]
        s = r["summary"]
        self.assertEqual((s["total_completed_and_stored_to_date"], s["total_retainage"]), (156000, 14850))
        self.assertEqual((s["total_earned_less_retainage"], s["current_payment_due"], s["balance_to_finish_including_retainage"]), (141150, 51150, 48850))
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("Doors", r["warnings"][0])
        self.assertTrue(call("pay_application", lines=[], retainage_percent=10)["isError"])

    def test_change_order_markup(self):
        r = call("change_order_markup", labor=10000, labor_burden_percent=35, material=5000, subcontract=20000,
                 overhead_percent=10, profit_percent=10, subcontract_markup_percent=5, bond_percent=1)["structuredContent"]
        # own forces 18,500 + OH 1,850 + profit 2,035 on cost plus OH; sub 20,000 + 1,000; bond 1% of 43,385
        self.assertEqual(r["total"], 43818.85)
        self.assertEqual(r["direct_cost_with_burden"], 38500)
        flat = call("change_order_markup", material=1000, overhead_percent=10, profit_percent=10, profit_on_overhead=False)["structuredContent"]
        self.assertEqual(flat["total"], 1200)
        self.assertEqual(call("change_order_markup", material=1000, overhead_percent=10, profit_percent=10)["structuredContent"]["total"], 1210)
        self.assertTrue(call("change_order_markup", overhead_percent=10)["isError"])

    def test_prompts(self):
        replies = session(
            INIT,
            {"jsonrpc": "2.0", "id": 1, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "draft_rfi", "arguments": {"issue": "Beam B4 clashes with the duct on grid C", "references": "S-201 detail 4"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "prompts/get", "params": {"name": "draft_rfi", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "no_such_prompt"}},
        )
        names = [p["name"] for p in replies[1]["result"]["prompts"]]
        self.assertEqual(names, ["draft_rfi", "check_pay_application", "review_change_order", "schedule_review", "quantity_takeoff"])
        self.assertTrue(all("render" not in p for p in replies[1]["result"]["prompts"]))
        message = replies[2]["result"]["messages"][0]
        self.assertEqual(message["role"], "user")
        self.assertIn("Beam B4", message["content"]["text"])
        self.assertIn("S-201 detail 4", message["content"]["text"])
        self.assertEqual(replies[3]["error"]["code"], -32602)
        self.assertEqual(replies[4]["error"]["code"], -32602)

    def test_prompts_only_name_real_tools(self):
        from construction_mcp.prompts import PROMPTS

        for prompt in PROMPTS:
            text = prompt["render"]({a["name"]: "x" for a in prompt["arguments"]})
            for word in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text):
                self.assertIn(word, HANDLERS, f"{prompt['name']} mentions {word}, which is not a tool")

    def test_readme_counts_match(self):
        from construction_mcp.prompts import PROMPTS

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"badge/tools-{len(TOOLS)}-", readme)
        self.assertIn(f"badge/prompts-{len(PROMPTS)}-", readme)
        for tool in TOOLS:
            self.assertIn(f"| `{tool['name']}` |", readme, f"{tool['name']} is missing from the README tables")

    def test_in_process_server(self):
        server = Server(TOOLS, HANDLERS)
        reply = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "define_term", "arguments": {"term": "retainage"}}})
        self.assertFalse(reply["result"]["isError"])
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}))


SRC = {"system": "procore", "method": "api"}
RECORDS = [
    {"type": "project", "id": "P-1", "name": "Sample", "source": SRC},
    {"type": "spec_section", "id": "SPEC-05-12-00", "project_id": "P-1", "number": "05 12 00", "source": SRC},
    {"type": "rfi", "id": "RFI-011", "project_id": "P-1", "number": "RFI-011", "subject": "Support for relocated unit", "status": "answered",
     "created_date": "2026-08-17", "spec_section_ids": ["SPEC-05-12-00"], "source": SRC},
    {"type": "change_event", "id": "CE-004", "project_id": "P-1", "number": "CE-004", "title": "Support steel", "origin": "rfi",
     "status": "priced", "event_date": "2026-08-20", "rfi_ids": ["RFI-011"], "source": SRC},
    {"type": "change_order", "id": "CO-004", "project_id": "P-1", "number": "CO-004", "level": "owner", "status": "pending",
     "amount": "18500.00", "change_event_ids": ["CE-004"], "source": SRC},
    {"type": "cost_line", "id": "INV-7-1", "project_id": "P-1", "posted_date": "2026-09-01", "amount": "900.00", "cost_code": "05-500",
     "cost_type": "subcontract", "change_order_id": "CO-004", "source": {"system": "pdf", "method": "document", "confidence": 0.91}},
]


class Records(unittest.TestCase):
    """The construction-schema tools: describe, validate, trace."""

    def test_describe(self):
        listing = call("schema_describe")["structuredContent"]
        self.assertEqual(len(listing["entities"]), 17)
        co = call("schema_describe", entity="change_order")["structuredContent"]
        self.assertIn("change_event_ids", [f["field"] for f in co["fields"]])
        self.assertIn("cost_line.change_order_id", co["pointed_at_by"])
        self.assertTrue(call("schema_describe", entity="widget")["isError"])

    def test_validate(self):
        ok = call("records_validate", records=RECORDS)["structuredContent"]
        self.assertTrue(ok["valid"], ok["findings"])
        bad = [dict(r) for r in RECORDS]
        bad[3]["rfi_ids"] = ["RFI-099"]
        bad[5]["source"] = {"system": "pdf", "method": "document"}
        found = call("records_validate", records=bad)["structuredContent"]
        self.assertFalse(found["valid"])
        self.assertEqual(sorted(f["check"] for f in found["findings"]), ["B09", "B11"])
        self.assertTrue(call("records_validate", records=[])["isError"])

    def test_trace(self):
        r = call("records_trace", records=RECORDS, start="rfi:RFI-011")["structuredContent"]
        reached = [(x["record"]["type"], x["record"]["id"]) for x in r["records"]]
        self.assertEqual(reached[0], ("rfi", "RFI-011"))
        for key in [("spec_section", "SPEC-05-12-00"), ("change_event", "CE-004"), ("change_order", "CO-004"), ("cost_line", "INV-7-1")]:
            self.assertIn(key, reached)
        self.assertIn("via document, confidence 0.91", r["brief"])
        self.assertTrue(call("records_trace", records=RECORDS, start="rfi:RFI-404")["isError"])
        self.assertTrue(call("records_trace", records=RECORDS, start="RFI-011")["isError"])


class Http(unittest.TestCase):
    """The Streamable HTTP transport, run in-process on a free port."""

    def start(self, **options) -> str:
        from construction_mcp.http_transport import serve_http
        from construction_mcp.prompts import PROMPTS, get_prompt

        ready, holder = threading.Event(), {}
        server = Server(TOOLS, HANDLERS, None, PROMPTS, get_prompt)
        thread = threading.Thread(target=serve_http, args=(server,), kwargs=dict(port=0, ready=ready, holder=holder, **options), daemon=True)
        with contextlib.redirect_stderr(io.StringIO()):
            thread.start()
            ready.wait(5)
        self.addCleanup(holder["httpd"].shutdown)
        return f"http://127.0.0.1:{holder['port']}"

    def post(self, url: str, body, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(headers or {})})
        return self.open(request)

    def open(self, request):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                text = response.read().decode()
                return response.status, dict(response.headers), json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            text = error.read().decode()
            return error.code, dict(error.headers), json.loads(text) if text else None

    def test_round_trip(self):
        base = self.start()
        status, _, body = self.post(base + "/mcp", INIT)
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["serverInfo"]["name"], "construction-reference")
        status, _, body = self.post(base + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "markup_margin", "arguments": {"cost": 100, "markup_percent": 20}}}, {"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual((status, body["result"]["structuredContent"]["price"]), (200, 120.0))
        status, _, body = self.post(base + "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, body), (202, None))
        status, _, body = self.open(urllib.request.Request(base + "/health"))
        self.assertEqual((status, body["status"]), (200, "ok"))
        self.assertEqual(self.open(urllib.request.Request(base + "/mcp"))[0], 405, "no server-sent event stream")
        self.assertEqual(self.open(urllib.request.Request(base + "/elsewhere"))[0], 404)

    def test_rejections(self):
        base = self.start()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.post(base + "/mcp", ping, {"MCP-Protocol-Version": "1999-01-01"})[0], 400)
        self.assertEqual(self.post(base + "/mcp", b"{not json")[2]["error"]["code"], -32700)
        self.assertEqual(self.post(base + "/mcp", ping, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.post(base + "/mcp", b"x" * 1_000_001)[0], 413)
        # DNS rebinding: a page on another site must not reach a local server
        self.assertEqual(self.post(base + "/mcp", ping, {"Origin": "https://evil.example"})[0], 403)
        status, headers, _ = self.post(base + "/mcp", ping, {"Origin": "http://localhost:6274"})
        self.assertEqual((status, headers.get("Access-Control-Allow-Origin")), (200, "http://localhost:6274"))

    def test_allowed_origin_and_token(self):
        base = self.start(token="s3cret", allowed_origins=["https://app.example"])
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        status, headers, _ = self.post(base + "/mcp", ping)
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers["WWW-Authenticate"])
        self.assertEqual(self.post(base + "/mcp", ping, {"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.post(base + "/mcp", ping, {"Authorization": "Bearer s3cret", "Origin": "https://app.example"})[0], 200)
        self.assertEqual(self.post(base + "/mcp", ping, {"Authorization": "Bearer s3cret", "Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.open(urllib.request.Request(base + "/health"))[0], 200, "health needs no token")

    def test_keep_alive_connection_survives_rejections(self):
        import http.client

        base = self.start(token="t")
        conn = http.client.HTTPConnection(base.replace("http://", ""), timeout=10)
        self.addCleanup(conn.close)
        ping = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        good = {"Content-Type": "application/json", "Authorization": "Bearer t"}
        for _ in range(2):
            conn.request("POST", "/mcp", ping, good)
            response = conn.getresponse()
            self.assertEqual((response.status, json.loads(response.read())["result"]), (200, {}))
        # a rejected request with an unread body must not leak that body into the next request
        conn.request("POST", "/mcp", ping, {"Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        self.assertEqual((response.status, response.getheader("Connection")), (401, "close"))
        conn.close()
        conn.request("POST", "/mcp", ping, good)
        self.assertEqual(conn.getresponse().status, 200)

    def test_rate_limit(self):
        base = self.start(rate_limit_per_minute=3)
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        codes = [self.post(base + "/mcp", ping)[0] for _ in range(5)]
        self.assertEqual(codes[:3], [200, 200, 200])
        self.assertEqual(codes[-1], 429)

    def test_command_line(self):
        proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--http", "--port", "0"], stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        line = proc.stderr.readline()
        url = re.search(r"http://\S+", line).group(0)
        status, _, body = self.post(url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual((status, len(body["result"]["tools"])), (200, len(TOOLS)))


if __name__ == "__main__":
    unittest.main()
