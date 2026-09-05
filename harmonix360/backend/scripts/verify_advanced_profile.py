"""Verify the MCP server over real Streamable HTTP against a fresh image.

Does a genuine JSON-RPC handshake, lists tools, calls a read tool, and checks
the RBAC boundary that matters: a valid agent key acting for an HR Manager must
still be refused on payroll.
"""
import json, sys, uuid
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8100/mcp"
KEY = sys.argv[2] if len(sys.argv) > 2 else "peoplepay360-mcp-dev-key"

HEADERS = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream"}
failures = []

def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(label)
    return cond

def parse(resp):
    """Streamable HTTP replies either as JSON or as an SSE frame."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ValueError(f"no data frame in SSE: {resp.text[:300]}")
    return resp.json()

def rpc(client, sid, method, params=None, notify=False):
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = str(uuid.uuid4())
    h = dict(HEADERS)
    if sid:
        h["Mcp-Session-Id"] = sid
    r = client.post(BASE, json=body, headers=h)
    return r

with httpx.Client(timeout=60.0) as client:
    print("\n== handshake ==")
    r = client.post(BASE, json={
        "jsonrpc": "2.0", "id": "init", "method": "initialize",
        "params": {"protocolVersion": "2024-11-05",
                   "capabilities": {},
                   "clientInfo": {"name": "verify", "version": "1"}}},
        headers=HEADERS)
    check("initialize returned 200", r.status_code == 200, f"http={r.status_code}")
    sid = r.headers.get("mcp-session-id")
    init = parse(r)
    server_name = (init.get("result", {}).get("serverInfo", {}) or {}).get("name")
    check("server identified itself", bool(server_name), f"name={server_name!r}")
    check("session id issued", bool(sid), f"sid={(sid or '')[:12]}...")

    rpc(client, sid, "notifications/initialized", {}, notify=True)

    print("\n== tools/list ==")
    r = rpc(client, sid, "tools/list", {})
    tools = parse(r).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check("tools listed", len(tools) > 0, f"{len(tools)} tools")
    print("   " + ", ".join(names))
    check("compute tool is absent (no AI write path to the rule engine)",
          not any("compute" in n for n in names))
    for expected in ("get_employee", "get_payslip", "get_attendance_summary",
                     "create_payrun", "query_audit_trail"):
        check(f"tool present: {expected}", expected in names)

    print("\n== read tool over MCP (payroll manager) ==")
    r = rpc(client, sid, "tools/call", {
        "name": "get_employee",
        "arguments": {"employee_id": "emp_wG2A92xE",
                      "actor_email": "payroll.manager@peoplepay360.com",
                      "api_key": KEY}})
    payload = parse(r)
    res = payload.get("result", {})
    text = "".join(c.get("text", "") for c in res.get("content", []))
    ok = "emp_wG2A92xE" in text
    check("get_employee returned the seeded employee", ok, text[:160].replace("\n", " "))

    print("\n== RBAC: a valid agent key must NOT widen the actor ==")
    r = rpc(client, sid, "tools/call", {
        "name": "create_payrun",
        "arguments": {"name": "should-not-exist", "salary_structure_id": "sstr_x",
                      "period_start": "2026-08-01", "period_end": "2026-08-31",
                      "employee_ids": ["emp_wG2A92xE"],
                      "actor_email": "hr.manager@peoplepay360.com",
                      "api_key": KEY}})
    payload = parse(r)
    blob = json.dumps(payload).lower()
    denied = ("403" in blob or "forbid" in blob or "permission" in blob
              or "not authorised" in blob or "not authorized" in blob)
    check("HR Manager is denied payroll through MCP", denied,
          json.dumps(payload)[:220])

    print("\n== a bad agent key is rejected ==")
    r = rpc(client, sid, "tools/call", {
        "name": "get_employee",
        "arguments": {"employee_id": "emp_wG2A92xE",
                      "actor_email": "payroll.manager@peoplepay360.com",
                      "api_key": "wrong-key"}})
    blob = json.dumps(parse(r)).lower()
    check("invalid api_key rejected", "key" in blob and ("invalid" in blob or "unauth" in blob or "401" in blob),
          json.dumps(parse(r))[:200])

print("\n" + "=" * 60)
print(f"{len(failures)} failure(s)" if failures else "ALL MCP CHECKS PASSED")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
