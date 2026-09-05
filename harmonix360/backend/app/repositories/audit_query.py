"""
Read-only repository for querying the immutable audit_logs table.
Used by MCP's query_audit_trail tool.
"""
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import AuditLog


class AuditQueryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_entity_id(
        self, entity_public_id: str, limit: int = 20
    ) -> List[AuditLog]:
        """Query audit_logs for a specific entity by public_id."""
        stmt = (
            select(AuditLog)
            .where(AuditLog.entity_id == entity_public_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
