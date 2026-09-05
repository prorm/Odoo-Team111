from typing import Optional
from sqlalchemy import select
from app.models.entities import Asset
from app.repositories.base import BaseRepository

class AssetRepository(BaseRepository[Asset]):
    model = Asset
    public_id_prefix = "ast"

    async def get_by_asset_tag(self, asset_tag: str, tenant_id: str = "default") -> Optional[Asset]:
        stmt = select(Asset).where(Asset.asset_tag == asset_tag, Asset.tenant_id == tenant_id, Asset.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
