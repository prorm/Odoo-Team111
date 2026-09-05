import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def get_admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@harmonix360.com", "password": "admin123"})
    return resp.json()["access_token"]

def run_branch_tests():
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("--- TEST 1: ESCALATE (High Value Asset > $5000) ---")
    # Create high value asset
    asset_data = {
        "name": "Enterprise Server Cluster",
        "asset_tag": f"TAG-HIGH-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "NEW",
        "location": "DataCenter-1",
        "purchase_cost": 12000.0, # > $5000
        "is_bookable": True
    }
    r = requests.post(f"{BASE_URL}/assets/", json=asset_data, headers=headers)
    asset = r.json()
    asset_id = asset["id"]
    print(f"Created High-Value Asset: {asset_id} (Cost: ${asset['purchase_cost']})")

    # Request transfer
    t_data = {
        "asset_public_id": asset_id,
        "to_user_id": 2,
        "reason": "Routine server move to rack 4"
    }
    r_t = requests.post(f"{BASE_URL}/transfers/", json=t_data, headers=headers)
    transfer = r_t.json()
    transfer_id = transfer["transfer"]["id"]

    # Poll status
    time.sleep(3)
    r_poll = requests.get(f"{BASE_URL}/transfers/{transfer_id}", headers=headers)
    print("Escalate Branch Result:", json.dumps(r_poll.json(), indent=2))

    print("\n--- TEST 2: REJECT (Invalid Reason) ---")
    # Create normal asset
    asset_data_2 = {
        "name": "Gaming Monitor",
        "asset_tag": f"TAG-GAMING-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "NEW",
        "location": "HQ",
        "purchase_cost": 400.0,
        "is_bookable": True
    }
    r2 = requests.post(f"{BASE_URL}/assets/", json=asset_data_2, headers=headers)
    asset2 = r2.json()
    asset_id2 = asset2["id"]
    print(f"Created Asset: {asset_id2}")

    # Request transfer with invalid reason
    t_data_2 = {
        "asset_public_id": asset_id2,
        "to_user_id": 2,
        "reason": "I want to take this home for personal gaming on weekends."
    }
    r_t2 = requests.post(f"{BASE_URL}/transfers/", json=t_data_2, headers=headers)
    transfer2 = r_t2.json()
    transfer_id2 = transfer2["transfer"]["id"]

    # Poll status
    time.sleep(3)
    r_poll2 = requests.get(f"{BASE_URL}/transfers/{transfer_id2}", headers=headers)
    print("Reject Branch Result:", json.dumps(r_poll2.json(), indent=2))

if __name__ == "__main__":
    run_branch_tests()
