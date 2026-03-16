"""
Pydantic response models for the Approval API.

Sub-milestone: 1.2
"""
from pydantic import BaseModel
from typing import Optional


class MessagePreview(BaseModel):
    uid: int
    subject: Optional[str]
    sender: Optional[str]
    date_sent: Optional[int]


class OperationResponse(BaseModel):
    id: str
    status: str
    op_type: str
    description: str
    created_at: int
    decided_at: Optional[int]
    batch_id: Optional[str]
    message_previews: list[MessagePreview] = []


class AuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: Optional[str]
    event: str
    actor: Optional[str]
    detail: Optional[str]
