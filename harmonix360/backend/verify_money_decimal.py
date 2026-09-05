"""Live proof that money in Harmonix360 is exact (Part 6 / migration 008).

Exercises the real stack — SQLAlchemy model -> asyncpg -> Postgres
numeric(12, 2) -> back into Python — rather than asserting things about
Decimal in isolation, because the claim being proven is about the *column*
and the round-trip, not about Python's decimal module.

    python verify_money_decimal.py

Requires DATABASE_URL to point at a database migrated to >= 012_money_as_numeric.
Creates a throwaway asset category + assets and deletes them again; the whole
run leaves no rows behind.
"""
import asyncio
from decimal import Decimal

from sqlalchemy import delete, func, select, text

from app.core.database import AsyncSessionLocal
from app.models.entities import Asset, AssetCategory

TAG_PREFIX = "MONEYPROOF-"

# format_type() renders the column's real type the way psql's \d does, i.e.
# "numeric(12,2)" rather than information_schema's split-out precision/scale.
COLUMN_TYPE_SQL = text("""
    SELECT format_type(a.atttypid, a.atttypmod)
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    WHERE c.relname = 'assets' AND a.attname = 'purchase_cost'
""")


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


async def main() -> None:
    failures: list[str] = []

    async with AsyncSessionLocal() as db:
        # ---------------------------------------------------------------- setup
        category = AssetCategory(public_id="cat_moneyproof", name="Money Proof Category")
        db.add(category)
        await db.flush()
        # Held as a plain int: the ORM instance would need a lazy refresh if it
        # ever got expired, and refreshing lazily from async teardown code is
        # exactly the MissingGreenlet trap.
        category_id = category.id

        banner("0. WHAT THE COLUMN ACTUALLY IS")
        col_type = (await db.execute(COLUMN_TYPE_SQL)).scalar_one()
        print(f"assets.purchase_cost -> {col_type}")
        if col_type != "numeric(12,2)":
            failures.append(f"column type is {col_type}, expected numeric(12,2)")

        # ------------------------------------------------- case 1: 0.10 + 0.20
        banner("1. 0.10 + 0.20 STORED AND SUMMED IN POSTGRES  (EXPECT exactly 0.30)")
        for i, amount in enumerate(("0.10", "0.20")):
            db.add(Asset(
                public_id=f"ast_moneyproof_a{i}",
                name=f"Money Proof A{i}",
                asset_tag=f"{TAG_PREFIX}A{i}",
                category_id=category_id,
                purchase_cost=Decimal(amount),
            ))
        await db.flush()

        total_a = (await db.execute(
            select(func.sum(Asset.purchase_cost)).where(Asset.asset_tag.like(f"{TAG_PREFIX}A%"))
        )).scalar_one()

        print(f"SQL SUM(purchase_cost)      = {total_a!r}")
        print(f"python type                 = {type(total_a).__name__}")
        print(f"total == Decimal('0.30')    -> {total_a == Decimal('0.30')}")
        print(f"float equivalent (0.1+0.2)  = {0.1 + 0.2!r}   <- what Float would have given")
        print(f"float 0.1+0.2 == 0.3        -> {0.1 + 0.2 == 0.3}")
        if not isinstance(total_a, Decimal):
            failures.append(f"SUM returned {type(total_a).__name__}, expected Decimal")
        if total_a != Decimal("0.30"):
            failures.append(f"0.10 + 0.20 summed to {total_a}, expected 0.30")

        # ------------------------------------------ case 2: ten times 0.10
        banner("2. TEN ROWS OF 0.10 SUMMED IN POSTGRES  (EXPECT exactly 1.00)")
        for i in range(10):
            db.add(Asset(
                public_id=f"ast_moneyproof_b{i}",
                name=f"Money Proof B{i}",
                asset_tag=f"{TAG_PREFIX}B{i}",
                category_id=category_id,
                purchase_cost=Decimal("0.10"),
            ))
        await db.flush()

        total_b = (await db.execute(
            select(func.sum(Asset.purchase_cost)).where(Asset.asset_tag.like(f"{TAG_PREFIX}B%"))
        )).scalar_one()

        float_running = 0.0
        for _ in range(10):
            float_running += 0.1

        print(f"SQL SUM of ten 0.10 rows    = {total_b!r}")
        print(f"total == Decimal('1.00')    -> {total_b == Decimal('1.00')}")
        print(f"float running total         = {float_running!r}   <- what Float would have given")
        print(f"float total == 1.0          -> {float_running == 1.0}")
        if total_b != Decimal("1.00"):
            failures.append(f"ten 0.10 rows summed to {total_b}, expected 1.00")

        # ------------------------------- case 3: Python-side accumulation
        banner("3. ACCUMULATING THE VALUES READ BACK FROM THE DB IN PYTHON")
        rows = (await db.execute(
            select(Asset.purchase_cost).where(Asset.asset_tag.like(f"{TAG_PREFIX}B%"))
        )).scalars().all()

        py_total = sum(rows, Decimal("0"))
        print(f"values read back            = {[str(r) for r in rows]}")
        print(f"element type                = {type(rows[0]).__name__}")
        print(f"python sum()                = {py_total!r}")
        print(f"py_total == Decimal('1.00') -> {py_total == Decimal('1.00')}")
        if not all(isinstance(r, Decimal) for r in rows):
            failures.append("purchase_cost did not come back from the DB as Decimal")
        if py_total != Decimal("1.00"):
            failures.append(f"python accumulation gave {py_total}, expected 1.00")

        # -------------------------------------- case 4: round-trip fidelity
        banner("4. SINGLE-VALUE ROUND TRIP  (EXPECT stored == written, to the cent)")
        db.add(Asset(
            public_id="ast_moneyproof_c",
            name="Money Proof C",
            asset_tag=f"{TAG_PREFIX}C",
            category_id=category_id,
            purchase_cost=Decimal("1899.99"),
        ))
        await db.flush()

        stored = (await db.execute(
            select(Asset.purchase_cost).where(Asset.asset_tag == f"{TAG_PREFIX}C")
        )).scalar_one()
        print(f"wrote Decimal('1899.99'), read back {stored!r}")
        print(f"exact match                 -> {stored == Decimal('1899.99')}")
        if stored != Decimal("1899.99"):
            failures.append(f"1899.99 round-tripped as {stored}")

        # -------------------------------------------------------------- teardown
        await db.execute(delete(Asset).where(Asset.asset_tag.like(f"{TAG_PREFIX}%")))
        await db.execute(delete(AssetCategory).where(AssetCategory.id == category_id))
        await db.commit()

    banner("RESULT")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        raise SystemExit(1)
    print("All money assertions passed — purchase_cost is exact decimal end to end.")


if __name__ == "__main__":
    asyncio.run(main())
