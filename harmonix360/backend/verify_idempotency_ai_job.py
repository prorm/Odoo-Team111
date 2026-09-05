"""Live proof that Idempotency-Key covers the AI-job-creating endpoint (Part 7).

POST /api/v1/transfers/ is the Week 2 endpoint that both creates a row AND
enqueues a Taskiq job (evaluate_transfer_decision). Replaying it must not
produce a second transfer or a second job.

Harmonix360 applies idempotency as ONE global middleware
(app/middleware/idempotency.py, registered in app/main.py) rather than per
router, so this endpoint is covered by the same mechanism as Week 1's asset
creation. This script verifies that claim end to end instead of assuming it
from the registration line.

    python verify_idempotency_ai_job.py

Requires Postgres (migrated) and Redis. Does NOT require a running Taskiq
worker — in fact it is better without one: jobs enqueued during the run stay in
the Redis list where they can be counted. Everything it creates is cleaned up.
"""
import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.database import AsyncSessionLocal
from app.core.redis import redis_client
from app.jobs.broker import broker
from app.main import app
from app.models.entities import Asset, AssetCategory, TransferRequest

TASKIQ_QUEUE = broker.queue_name
IDEMPOTENCY_KEY = f"verify-{uuid.uuid4()}"
ASSET_TAG = "IDEMPROOF-1"


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


async def queue_depth() -> int:
    return await redis_client.llen(TASKIQ_QUEUE)


async def transfer_count(asset_id: int) -> int:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(func.count(TransferRequest.id)).where(TransferRequest.asset_id == asset_id)
        )).scalar_one()


async def main() -> None:
    failures: list[str] = []

    # ------------------------------------------------------------------ setup
    async with AsyncSessionLocal() as db:
        category = AssetCategory(public_id="cat_idemproof", name="Idempotency Proof Category")
        db.add(category)
        await db.flush()
        asset = Asset(
            public_id="temp", name="Idempotency Proof Asset",
            asset_tag=ASSET_TAG, category_id=category.id,
        )
        db.add(asset)
        await db.flush()
        from app.core.security import encode_public_id
        asset.public_id = encode_public_id(asset.id, "ast")
        await db.commit()
        asset_id, asset_public_id, category_id = asset.id, asset.public_id, category.id

    payload = {
        "asset_public_id": asset_public_id,
        "reason": "Idempotency verification — replayed request",
    }
    headers = {"Idempotency-Key": IDEMPOTENCY_KEY}

    banner("0. STARTING STATE")
    depth_before = await queue_depth()
    print(f"Idempotency-Key           = {IDEMPOTENCY_KEY}")
    print(f"asset                     = {asset_public_id}")
    print(f"taskiq queue '{TASKIQ_QUEUE}' depth = {depth_before}")
    print(f"transfers for this asset  = {await transfer_count(asset_id)}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://verify") as client:
        # -------------------------------------------------------- first request
        banner("1. FIRST REQUEST  (expect: 202, one transfer created, one job enqueued)")
        first = await client.post("/api/v1/transfers/", json=payload, headers=headers)
        print(f"HTTP {first.status_code}")
        first_body = first.json()
        print(f"transfer id  = {first_body['transfer']['id']}")
        print(f"ai_job_id    = {first_body['ai_job_id']}")

        depth_after_first = await queue_depth()
        count_after_first = await transfer_count(asset_id)
        print(f"queue depth  = {depth_before} -> {depth_after_first}  (delta {depth_after_first - depth_before})")
        print(f"transfer rows for asset = {count_after_first}")

        if first.status_code != 202:
            failures.append(f"first request returned {first.status_code}, expected 202")
        if depth_after_first - depth_before != 1:
            failures.append(f"first request enqueued {depth_after_first - depth_before} jobs, expected 1")
        if count_after_first != 1:
            failures.append(f"first request created {count_after_first} transfers, expected 1")

        # ------------------------------------------------------- second request
        banner("2. REPLAY, IDENTICAL Idempotency-Key  (expect: cached, NO new job, NO new row)")
        second = await client.post("/api/v1/transfers/", json=payload, headers=headers)
        print(f"HTTP {second.status_code}")
        second_body = second.json()
        print(f"transfer id  = {second_body['transfer']['id']}")
        print(f"ai_job_id    = {second_body['ai_job_id']}")
        print(f"X-Cache-Lookup header = {second.headers.get('x-cache-lookup')!r}")

        depth_after_second = await queue_depth()
        count_after_second = await transfer_count(asset_id)
        print(f"queue depth  = {depth_after_first} -> {depth_after_second}  (delta {depth_after_second - depth_after_first})")
        print(f"transfer rows for asset = {count_after_second}")

        print("\nbodies byte-identical -> "
              f"{first.content == second.content}")

        if depth_after_second != depth_after_first:
            failures.append(
                f"replay enqueued {depth_after_second - depth_after_first} extra job(s), expected 0"
            )
        if count_after_second != 1:
            failures.append(f"replay left {count_after_second} transfers, expected 1")
        if first_body["ai_job_id"] != second_body["ai_job_id"]:
            failures.append("replay returned a different ai_job_id — response was not cached")
        if first.content != second.content:
            failures.append("replay body differed from the original")

        # ---------------------------------------- control: a DIFFERENT key works
        banner("3. CONTROL — a DIFFERENT Idempotency-Key  (expect: NEW job + NEW row)")
        third = await client.post(
            "/api/v1/transfers/", json=payload,
            headers={"Idempotency-Key": f"verify-{uuid.uuid4()}"},
        )
        depth_after_third = await queue_depth()
        count_after_third = await transfer_count(asset_id)
        print(f"HTTP {third.status_code}")
        print(f"queue depth  = {depth_after_second} -> {depth_after_third}  (delta {depth_after_third - depth_after_second})")
        print(f"transfer rows for asset = {count_after_third}")
        print("(this is the control: it proves step 2's zero-delta came from the KEY,")
        print(" not from the endpoint being incapable of enqueuing twice)")

        if depth_after_third - depth_after_second != 1:
            failures.append("control request with a fresh key did not enqueue a job")
        if count_after_third != 2:
            failures.append(f"control request left {count_after_third} transfers, expected 2")

    # --------------------------------------------------------------- teardown
    async with AsyncSessionLocal() as db:
        await db.execute(delete(TransferRequest).where(TransferRequest.asset_id == asset_id))
        await db.execute(delete(Asset).where(Asset.id == asset_id))
        await db.execute(delete(AssetCategory).where(AssetCategory.id == category_id))
        await db.commit()
    # Remove only the jobs this run enqueued, leaving anything that was already
    # queued untouched. ListQueueBroker LPUSHes, so our jobs are the newest
    # entries at the head; dropping the first `added` of them restores the
    # queue we found.
    added = await queue_depth() - depth_before
    if added > 0:
        await redis_client.ltrim(TASKIQ_QUEUE, added, -1)
    await redis_client.delete(f"idempotency:{IDEMPOTENCY_KEY}")
    print(f"\ncleanup: removed {added} enqueued job(s); queue depth back to {await queue_depth()}")

    banner("RESULT")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        raise SystemExit(1)
    print("Idempotency-Key correctly de-duplicates the AI-job-creating endpoint:")
    print("  same key    -> 1 transfer, 1 job, byte-identical cached response")
    print("  fresh key   -> new transfer, new job")


if __name__ == "__main__":
    asyncio.run(main())
