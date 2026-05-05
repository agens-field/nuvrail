"""
Pydantic response models for the Approval API.

Sub-milestone: 1.0
"""
from __future__ import annotations

import json
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator


class MessagePreview(BaseModel):
    """Sender and subject for a single message affected by an IMAP operation."""
    uid: int
    sender: Optional[str] = None
    subject: Optional[str] = None
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
    is_urgent: int = 0
    error: Optional[str] = None
    message_previews: List[MessagePreview] = []

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


# ---------------------------------------------------------------------------
# Batch operation models
# ---------------------------------------------------------------------------


class BatchApproveRequest(BaseModel):
    operation_ids: List[str]


class BatchRejectRequest(BaseModel):
    operation_ids: List[str]


class BatchApproveResult(BaseModel):
    id: str
    status: str  # 'executed' | 'failed' | 'skipped'
    executed_at: Optional[int] = None
    error: Optional[str] = None  # set if status='failed'


class BatchRejectResult(BaseModel):
    id: str
    status: str  # 'rejected' | 'failed' | 'skipped'
    error: Optional[str] = None


class BatchApproveResponse(BaseModel):
    approved: List[BatchApproveResult]
    failed: List[BatchApproveResult]
    skipped: List[BatchApproveResult]  # not pending (already decided) or not found
    total: int


class BatchRejectResponse(BaseModel):
    rejected: List[BatchRejectResult]
    failed: List[BatchRejectResult]
    skipped: List[BatchRejectResult]  # not pending (already decided) or not found
    total: int


# ---------------------------------------------------------------------------
# Auto-approval rule models
# ---------------------------------------------------------------------------


class AutoApprovalRule(BaseModel):
    id: int
    enabled: bool
    priority: int
    op_type: Optional[str] = None
    sender_pattern: Optional[str] = None
    folder_from: Optional[str] = None
    action: Literal["approve", "reject"]
    description: str
    created_at: int


class AutoApprovalRuleCreateRequest(BaseModel):
    enabled: bool = True
    priority: int = 0
    op_type: Optional[str] = None
    sender_pattern: Optional[str] = None
    folder_from: Optional[str] = None
    action: Literal["approve", "reject"]
    description: str


class AutoApprovalRuleUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    op_type: Optional[str] = None
    sender_pattern: Optional[str] = None
    folder_from: Optional[str] = None
    action: Optional[Literal["approve", "reject"]] = None
    description: Optional[str] = None


class AuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: Optional[str] = None
    event: str
    actor: Optional[str] = None
    agent_id: Optional[str] = None
    agent_label: Optional[str] = None
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


# ---------------------------------------------------------------------------
# Auth + Agent credential models (Lane 2 / Lane 3)
# ---------------------------------------------------------------------------


class UserCreateRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None


class UserResponse(BaseModel):
    user_id: int
    email: str
    display_name: Optional[str] = None
    created_at: int


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    user_id: int
    email: str


class AgentCreateRequest(BaseModel):
    label: Optional[str] = "default"
    upstream_host: str
    upstream_imap_port: int = 993
    upstream_smtp_port: int = 587
    upstream_user: str
    # Password auth (mutually exclusive with OAuth2 fields below)
    upstream_password: Optional[str] = None
    # OAuth2 / XOAUTH2 fields (all required together if oauth2_provider is set)
    oauth2_provider: Optional[str] = None          # e.g. "google"
    oauth2_client_id: Optional[str] = None
    oauth2_client_secret: Optional[str] = None
    oauth2_refresh_token: Optional[str] = None


class AgentCreateResponse(BaseModel):
    id: int
    agent_username: str
    agent_token: str  # SHOWN ONCE — never returned again
    label: str
    upstream_host: str
    upstream_user: str


class AgentResponse(BaseModel):
    id: int
    agent_username: str
    label: str
    upstream_host: str
    upstream_user: str
    created_at: int
    revoked_at: Optional[int] = None


# ---------------------------------------------------------------------------
# Web Push models
# ---------------------------------------------------------------------------


class VapidKeyResponse(BaseModel):
    public_key: str


class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class PushSubscribeResponse(BaseModel):
    subscribed: bool
    endpoint: str
