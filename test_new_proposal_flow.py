import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def get_admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@harmonix360.com", "password": "admin123"})
    return resp.json()["access_token"]

def run_tests():
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("==================================================")
    print("SCENARIO 1: AI RECOMMENDS APPROVE -> PENDING_REVIEW -> HUMAN OVERRIDE APPROVE")
    print("==================================================")
    # 1. Create asset
    asset_data_1 = {
        "name": "Standard Workstation Laptop",
        "asset_tag": f"TAG-APP-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "GOOD",
        "location": "HQ",
        "purchase_cost": 1500.0,
        "is_bookable": True
    }
    r1 = requests.post(f"{BASE_URL}/assets/", json=asset_data_1, headers=headers)
    asset1 = r1.json()
    asset_id1 = asset1["id"]
    print(f"Created Asset: {asset_id1}")

    # 2. Request transfer (AI should recommend approve)
    t_data_1 = {
        "asset_public_id": asset_id1,
        "to_user_id": 2,
        "reason": "Department transfer for new developer joining team B"
    }
    r_t1 = requests.post(f"{BASE_URL}/transfers/", json=t_data_1, headers=headers)
    transfer1_id = r_t1.json()["transfer"]["id"]

    # 3. Poll status after AI processing
    time.sleep(3)
    r_poll1 = requests.get(f"{BASE_URL}/transfers/{transfer1_id}", headers=headers)
    poll1_data = r_poll1.json()
    print("\n--- AI Proposal Result (Approve Scenario) ---")
    print(json.dumps(poll1_data, indent=2))

    # 4. Human override -> APPROVE
    print("\n--- Applying Human Override (APPROVE) ---")
    r_override1 = requests.post(
        f"{BASE_URL}/transfers/{transfer1_id}/override",
        json={"decision": "approve", "note": "Human manager approved AI recommendation."},
        headers=headers
    )
    print(json.dumps(r_override1.json(), indent=2))


    print("\n==================================================")
    print("SCENARIO 2: AI RECOMMENDS REJECT -> PENDING_REVIEW -> HUMAN OVERRIDE REJECT")
    print("==================================================")
    # 1. Create asset
    asset_data_2 = {
        "name": "Personal Monitor",
        "asset_tag": f"TAG-REJ-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "GOOD",
        "location": "HQ",
        "purchase_cost": 300.0,
        "is_bookable": True
    }
    r2 = requests.post(f"{BASE_URL}/assets/", json=asset_data_2, headers=headers)
    asset2 = r2.json()
    asset_id2 = asset2["id"]
    print(f"Created Asset: {asset_id2}")

    # 2. Request transfer with invalid reason (AI should recommend reject)
    t_data_2 = {
        "asset_public_id": asset_id2,
        "to_user_id": 2,
        "reason": "I want to take this home for personal gaming on weekends."
    }
    r_t2 = requests.post(f"{BASE_URL}/transfers/", json=t_data_2, headers=headers)
    transfer2_id = r_t2.json()["transfer"]["id"]

    # 3. Poll status after AI processing
    time.sleep(3)
    r_poll2 = requests.get(f"{BASE_URL}/transfers/{transfer2_id}", headers=headers)
    poll2_data = r_poll2.json()
    print("\n--- AI Proposal Result (Reject Scenario) ---")
    print(json.dumps(poll2_data, indent=2))

    # 4. Human override -> REJECT
    print("\n--- Applying Human Override (REJECT) ---")
    r_override2 = requests.post(
        f"{BASE_URL}/transfers/{transfer2_id}/override",
        json={"decision": "reject", "note": "Human manager confirmed rejection per company policy."},
        headers=headers
    )
    print(json.dumps(r_override2.json(), indent=2))

if __name__ == "__main__":
    run_tests()
