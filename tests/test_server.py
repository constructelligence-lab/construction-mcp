"""End-to-end tests: start the server as an MCP client would, over stdio, and talk to it.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from construction_mcp.protocol import SUPPORTED_VERSIONS, Server  # noqa: E402
from construction_mcp.tools import HANDLERS, TOOLS  # noqa: E402


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
        self.assertEqual(len(uris), 8)
        self.assertTrue(all(u.startswith("construction-reference://data/") for u in uris))
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

    def test_no_network_code(self):
        source = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "construction_mcp").glob("*.py"))
        for module in ("socket", "urllib", "http.client", "requests", "ssl", "subprocess"):
            self.assertNotIn(f"import {module}", source)
            self.assertNotIn(f"from {module}", source)


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


if __name__ == "__main__":
    unittest.main()
