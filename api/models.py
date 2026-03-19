"""
Pydantic response models for the Approval API.

Sub-milestone: 1.0
"""
from __future__ import annotations

import json
from typing import List, Optional

from pydantic import BaseModel, field_validator


class MessagePreview(BaseModel):
    uid: int
    subject: Optional[str] = None
    sender: Optional[str] = None
    date_sent: Optional[int] = None


class OperationResponse(BaseModel):
    id: str
    status: str
    op_type: str
    protocol: str
    description: str
    created_at: int
    expires_at: int
    decided_at: Optional[int] = None
    imap_command: Optional[str] = None
    smtp_envelope: Optional[dict] = None
    message_ids: List[str] = []
    folder_from: Optional[str] = None
    folder_to: Optional[str] = None
    flags_add: List[str] = []
    flags_remove: List[str] = []
    error: Optional[str] = None

    @field_validator("smtp_envelope", mode="before")
    @classmethod
    def parse_smtp_envelope(cls, v: object) -> object:
        """Deserialize smtp_envelope from JSON string if stored as text in SQLite."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("message_ids", "flags_add", "flags_remove", mode="before")
    @classmethod
    def parse_json_list(cls, v: object) -> object:
        """Deserialize JSON-encoded list fields from SQLite TEXT columns."""
        if isinstance(v, str):
            return json.loads(v)
        if v is None:
            return []
        return v


class OperationListResponse(BaseModel):
    operations: List[OperationResponse]
    total: int


class ApproveResponse(BaseModel):
    id: str
    status: str
    executed_at: Optional[int] = None
    error: Optional[str] = None


class RejectResponse(BaseModel):
    id: str
    status: str


class AuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: Optional[str] = None
    event: str
    actor: Optional[str] = None
    agent_id: Optional[str] = None
    detail: Optional[dict] = None       # parsed JSON from audit_log.detail
    # Joined from staged_operations (None if operation record no longer exists)
    op_description: Optional[str] = None
    op_type: Optional[str] = None
    op_protocol: Optional[str] = None
    op_status: Optional[str] = None


class AuditListResponse(BaseModel):
    entries: List[AuditEntry]
    total: int
    limit: int
    offset: int
