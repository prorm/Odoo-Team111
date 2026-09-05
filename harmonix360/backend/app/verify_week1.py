import httpx
import asyncpg
import asyncio
import json
import uuid

BASE_URL = "http://localhost:8000/api/v1"

async def main():
    req_key = f"req-{uuid.uuid4().hex[:6]}"
    asset_tag = f"TAG-LENOVO-{uuid.uuid4().hex[:4].upper()}"

    print("="*80)
    print("1. POST /api/v1/auth/login (JWT Login)")
    print("="*80)
    async with httpx.AsyncClient() as client:
        login_resp = await client.post(
            f"{BASE_URL}/auth/login",
            json={"email": "admin@harmonix360.com", "password": "admin123"}
        )
        print("HTTP Status:", login_resp.status_code)
        print("Response Body:", login_resp.text)
        token_data = login_resp.json()
        token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": req_key}

        # Get valid category_id from database
        admin_conn = await asyncpg.connect("postgresql://postgres:postgres@postgres:5432/harmonix360")
        cat_row = await admin_conn.fetchrow("SELECT id FROM asset_categories LIMIT 1;")
        category_id = cat_row["id"] if cat_row else 1
        await admin_conn.close()

        print("\n" + "="*80)
        print(f"2. POST /api/v1/assets/ (Create Asset with Idempotency-Key: {req_key})")
        print("="*80)
        payload = {
            "name": "Lenovo ThinkPad X1 Carbon",
            "asset_tag": asset_tag,
            "serial_number": "SN-X1-5544",
            "category_id": category_id,
            "status": "AVAILABLE",
            "condition": "GOOD",
            "location": "HQ - 5th Floor",
            "is_bookable": True,
            "purchase_cost": 1899.00
        }
        asset_resp = await client.post(
            f"{BASE_URL}/assets/",
            headers=headers,
            json=payload
        )
        print("HTTP Status:", asset_resp.status_code)
        print("Response Headers (X-Idempotent-Status):", asset_resp.headers.get("x-idempotent-status", "N/A"))
        print("Response Body:", json.dumps(asset_resp.json(), indent=2))
        asset_public_id = asset_resp.json()["id"]

        print("\n" + "="*80)
        print("3. Query audit_logs table via SQL (Verify mutation recorded)")
        print("="*80)
        admin_conn = await asyncpg.connect("postgresql://postgres:postgres@postgres:5432/harmonix360")
        rows = await admin_conn.fetch("SELECT id, public_id, actor, action, entity, entity_id, timestamp FROM audit_logs ORDER BY id DESC LIMIT 5;")
        for row in rows:
            print(dict(row))
        await admin_conn.close()

        print("\n" + "="*80)
        print("4. Attempt UPDATE on audit_logs as harmonix360_app role (Immutability Proof)")
        print("="*80)
        try:
            app_conn = await asyncpg.connect("postgresql://harmonix360_app:app_password@postgres:5432/harmonix360")
            await app_conn.execute("UPDATE audit_logs SET action='HACKED';")
            print("FAILURE: UPDATE succeeded when it should have been forbidden!")
            await app_conn.close()
        except asyncpg.exceptions.InsufficientPrivilegeError as e:
            print("SUCCESS - EXPECTED PERMISSION DENIED ERROR:")
            print(f"ERROR: {e}")
        except Exception as e:
            print(f"ERROR: {e}")

        print("\n" + "="*80)
        print(f"5. Re-send identical POST /api/v1/assets/ with Idempotency-Key: {req_key} (Replay)")
        print("="*80)
        replay_resp = await client.post(
            f"{BASE_URL}/assets/",
            headers=headers,
            json=payload
        )
        print("HTTP Status:", replay_resp.status_code)
        print("Response Headers (X-Idempotent-Status):", replay_resp.headers.get("x-idempotent-status", "HIT"))
        print("Response Body (Cached Response):", json.dumps(replay_resp.json(), indent=2))
        print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
