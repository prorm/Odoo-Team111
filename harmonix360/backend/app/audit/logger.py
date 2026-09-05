import uuid
from typing import Optional, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import AuditLog

class AuditLogger:
    @staticmethod
    async def log_mutation(
        session: AsyncSession,
        actor: str,
        action: str,
        entity: str,
        entity_id: str,
        before_diff: Optional[Dict[str, Any]] = None,
        after_diff: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> AuditLog:
        # Generate immutable public_id upfront to prevent UPDATE query on audit_logs table
        public_id = f"aud_{uuid.uuid4().hex[:12]}"
        
        audit_entry = AuditLog(
            public_id=public_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before_diff=before_diff,
            after_diff=after_diff,
            reason=reason,
            request_id=request_id
        )
        session.add(audit_entry)
        await session.flush()
        return audit_entry
