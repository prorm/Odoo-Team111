from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel

MutationOp = Literal["CREATE", "UPDATE", "DELETE"]
MutationOutcome = Literal["applied", "conflict", "rejected"]
DeltaOp = Literal["CREATE", "UPDATE", "DELETE"]


class PullDeltaEntity(BaseModel):
    entity_type: str
    public_id: str
    op: DeltaOp
    version: int
    updated_at: datetime
    data: Optional[Dict[str, Any]] = None


class PullResponse(BaseModel):
    cursor: str
    server_time: datetime
    has_more: bool
    entities: List[PullDeltaEntity]


class PushMutation(BaseModel):
    client_mutation_id: str
    entity_type: str
    entity_id: Optional[str] = None
    op: MutationOp
    known_version: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None


class PushRequest(BaseModel):
    mutations: List[PushMutation]


class PushErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class PushMutationResult(BaseModel):
    client_mutation_id: str
    outcome: MutationOutcome
    entity_type: str
    entity_id: Optional[str] = None
    version: Optional[int] = None
    error: Optional[PushErrorDetail] = None


class PushResponse(BaseModel):
    results: List[PushMutationResult]
