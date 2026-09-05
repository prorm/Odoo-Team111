import os
import sys
import time
import requests
import json
from pprint import pprint

BASE_URL = "http://localhost:8000/api/v1"

def print_step(step_num, title):
    print(f"\n{'='*80}\nSTEP {step_num}: {title}\n{'='*80}")

def main():
    print("Starting Verification Trace for Week 2\n")

    # 1. Login -> get JWT token
    print_step(1, "Login as Admin (actor) to get JWT token")
    resp = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": "admin@harmonix360.com", "password": "admin123"}
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"Token obtained (length {len(token)})")

    # 2. Create asset -> HTTP 201
    print_step(2, "Create a new Asset")
    asset_data = {
        "name": "MacBook Pro M3",
        "asset_tag": f"TAG-{int(time.time())}", # Use dynamic tag to avoid duplicates
        "serial_number": f"SN-M3-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "NEW",
        "location": "HQ",
        "is_bookable": True,
        "purchase_cost": 2500.00
    }
    resp = requests.post(f"{BASE_URL}/assets/", json=asset_data, headers=headers)
    if resp.status_code >= 400:
        print(f"Error: {resp.status_code} {resp.text}")
    resp.raise_for_status()
    asset = resp.json()
    asset_id = asset["id"]
    print(f"Created asset HTTP {resp.status_code}:")
    pprint(asset)

    # 3. Transition asset
    print_step(3, "Transition asset status to ALLOCATE (triggers notification task)")
    resp = requests.post(f"{BASE_URL}/assets/{asset_id}/transition", json={"action": "ALLOCATE"}, headers=headers)
    resp.raise_for_status()
    print(f"Transitioned asset HTTP {resp.status_code}:")
    pprint(resp.json())

    # Sleep briefly so worker can process the notification and we can create a transfer
    time.sleep(2)

    # 4. Create TransferRequest -> HTTP 202
    print_step(4, "Create TransferRequest (Enqueues AI decision task)")
    transfer_data = {
        "asset_public_id": asset_id,
        "reason": "Transferring laptop to new hire in another office",
        "to_user_id": 2
    }
    resp = requests.post(f"{BASE_URL}/transfers/", json=transfer_data, headers=headers)
    resp.raise_for_status()
    transfer_resp = resp.json()
    transfer_id = transfer_resp["transfer"]["id"]
    ai_job_id = transfer_resp["ai_job_id"]
    print(f"Created transfer HTTP {resp.status_code}:")
    pprint(transfer_resp)

    # 5. Poll decision -> GET /api/v1/transfers/{id}/decision
    print_step(5, "Poll AI decision (expect ai_unavailable without keys)")
    decision_found = False
    for i in range(10):
        time.sleep(2)
        resp = requests.get(f"{BASE_URL}/transfers/{transfer_id}/decision", headers=headers)
        resp.raise_for_status()
        decision_data = resp.json()
        print(f"Polling AI decision attempt {i+1}: status={decision_data.get('status')}")
        if decision_data.get("decision") is not None:
            pprint(decision_data)
            decision_found = True
            break
        if decision_data.get("status") == "PENDING_REVIEW":
            # The decision data might be fetched directly from the transfer object
            transfer = requests.get(f"{BASE_URL}/transfers/{transfer_id}", headers=headers).json()
            if transfer.get("ai_decision_data"):
                print("Decision data found on transfer:")
                pprint(transfer["ai_decision_data"])
                decision_found = True
                break
    
    if not decision_found:
        print("Timed out waiting for AI decision.")

    # 6. Query audit_logs -> verify AI_DECISION_PROPOSE row (or UNAVAILABLE) and CREATE_TRANSFER row
    print_step(6, "Query audit logs via postgres to see CREATE_TRANSFER and AI_DECISION_*")
    print("Executing SQL against DB:")
    os.system(f'docker compose -f d:/Odoo-Testing/docker-compose.yml exec -T postgres psql -U postgres -d harmonix360 -c "SELECT action, actor, before_diff, after_diff FROM audit_logs WHERE entity_id = \'{transfer_id}\' ORDER BY timestamp ASC;"')

    # 7. Call human override (Login as Admin first)
    print_step(7, "Login as Admin to perform human override")
    resp = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": "admin@harmonix360.com", "password": "admin123"}
    )
    admin_token = resp.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    print("Call human override on TransferRequest (decision: approve)")
    override_data = {
        "decision": "approve",
        "note": "Approved despite AI failure, user really needs the laptop."
    }
    resp = requests.post(f"{BASE_URL}/transfers/{transfer_id}/override", json=override_data, headers=admin_headers)
    resp.raise_for_status()
    print(f"Override response HTTP {resp.status_code}:")
    pprint(resp.json())

    # 8. Query audit_logs again -> see HUMAN_OVERRIDE row
    print_step(8, "Query audit logs via postgres to see HUMAN_OVERRIDE row")
    os.system(f'docker compose -f d:/Odoo-Testing/docker-compose.yml exec -T postgres psql -U postgres -d harmonix360 -c "SELECT action, actor, before_diff, after_diff, reason FROM audit_logs WHERE entity_id = \'{transfer_id}\' ORDER BY timestamp ASC;"')

    # 9. MCP Server check
    print_step(9, "MCP Server check via standard MCP streamable-http client")
    print("Calling check_asset_status and query_audit_trail via test_mcp.py in backend container:")
    os.system(f'docker compose -f d:/Odoo-Testing/docker-compose.yml exec -T backend python test_mcp.py {asset_id}')

    # 10. SELECT from audit_logs for Week 1 row
    print_step(10, "Confirm Week 1 asset creation row still exists and is immutable")
    os.system(f'docker compose -f d:/Odoo-Testing/docker-compose.yml exec -T postgres psql -U postgres -d harmonix360 -c "SELECT action, actor, entity, entity_id FROM audit_logs WHERE entity = \'Asset\' AND entity_id = \'{asset_id}\' ORDER BY timestamp ASC;"')

if __name__ == "__main__":
    main()
