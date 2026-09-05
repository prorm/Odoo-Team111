import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def get_admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": "admin@harmonix360.com", "password": "admin123"})
    return resp.json()["access_token"]

def run_obs_tests():
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("=== 1. HIT HEALTH CHECK (FastAPI Span) ===")
    r1 = requests.get("http://localhost:8000/health")
    print("Health response:", r1.json())

    print("\n=== 2. HIT BOGUS ASSET PUBLIC ID (Exception / 404 Trace) ===")
    r2 = requests.get(f"{BASE_URL}/assets/bogus-nonexistent-id-999", headers=headers)
    print("404 response status:", r2.status_code)
    print("404 response body:", r2.json())

    print("\n=== 3. CREATE ASSET (FastAPI -> SQLAlchemy -> Redis Trace) ===")
    asset_data = {
        "name": "Obs Test Laptop",
        "asset_tag": f"TAG-OBS-{int(time.time())}",
        "category_id": 1,
        "department_id": 1,
        "condition": "GOOD",
        "location": "HQ",
        "purchase_cost": 1200.0,
        "is_bookable": True
    }
    r3 = requests.post(f"{BASE_URL}/assets/", json=asset_data, headers=headers)
    print("Asset create status:", r3.status_code)
    print("Asset data:", r3.json())

if __name__ == "__main__":
    run_obs_tests()
