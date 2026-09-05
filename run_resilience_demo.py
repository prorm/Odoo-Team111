import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def get_admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@harmonix360.com", "password": "admin123"})
    return resp.json()["access_token"]

def run_demo():
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("=== RESILIENCE DEMO: BOTH GROQ & CEREBRAS KEYS INVALID ===")

    # 1. Create asset
    asset_data = {
        "name": "Resilience Demo Asset",
        "asset_tag": f"TAG-DEMO-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "GOOD",
        "location": "HQ",
        "purchase_cost": 2000.0,
        "is_bookable": True
    }
    r = requests.post(f"{BASE_URL}/assets/", json=asset_data, headers=headers)
    asset_id = r.json()["id"]

    # 2. Fire transfer request (enqueues Taskiq worker job)
    t_data = {
        "asset_public_id": asset_id,
        "to_user_id": 2,
        "reason": "Resilience test transfer under total cloud AI outage"
    }
    r_t = requests.post(f"{BASE_URL}/transfers/", json=t_data, headers=headers)
    transfer_id = r_t.json()["transfer"]["id"]
    print(f"Transfer Request created: {transfer_id}")

    # 3. Poll result
    time.sleep(4)
    r_poll = requests.get(f"{BASE_URL}/transfers/{transfer_id}", headers=headers)
    print("\nResult after failure chain fallback:")
    print(json.dumps(r_poll.json(), indent=2))

if __name__ == "__main__":
    run_demo()
