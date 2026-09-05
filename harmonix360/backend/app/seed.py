import asyncio
import uuid
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.entities import User, AssetCategory
from app.core.security import get_password_hash
from hashids import Hashids

hashids = Hashids(salt="harmonix360-secret-salt-change-in-prod", min_length=8)

async def seed():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.email == "admin@harmonix360.com"))
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                email="admin@harmonix360.com",
                password_hash=get_password_hash("AdminPass123!"),
                name="System Admin",
                role="ADMIN",
                status="ACTIVE",
                public_id=f"usr_{uuid.uuid4().hex[:8]}"
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            user.public_id = hashids.encode(user.id)
            await db.commit()

        res_cat = await db.execute(select(AssetCategory).where(AssetCategory.name == "IT Equipment"))
        cat = res_cat.scalar_one_or_none()
        if not cat:
            cat = AssetCategory(
                name="IT Equipment",
                description="Laptops, Desktops, Servers",
                public_id=f"cat_{uuid.uuid4().hex[:8]}"
            )
            db.add(cat)
            await db.commit()
            await db.refresh(cat)
            cat.public_id = hashids.encode(cat.id)
            await db.commit()

        print(f"SEEDED: Admin User public_id={user.public_id}, Category public_id={cat.public_id}")

if __name__ == "__main__":
    asyncio.run(seed())
