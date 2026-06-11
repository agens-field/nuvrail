"""
Pydantic response models for the Approval API.

Sub-milestone: 1.0
"""
from __future__ import annotations

import json
from typing import List, Optional

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
    scheduled_execute_at: Optional[int] = None  # cool-down deadline (approve_after)
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
    batch_id: Optional[str] = None
    # Semantic intent derived at staging time (gateway.intent): 'archive',
    # 'delete', 'mark_spam', ... None = the op_type already says everything.
    intent_label: Optional[str] = None
    intent_confidence: Optional[float] = None

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
    # batch_id → one-line human summary, for batches with 2+ operations in
    # this response (gateway.batch_summary). The UI shows it as the batch
    # card's header instead of "Batch — N operations".
    batch_summaries: dict[str, str] = {}


class ApproveResponse(BaseModel):
    id: str
    status: str
    executed_at: Optional[int] = None
    error: Optional[str] = None


class RejectResponse(BaseModel):
    id: str
    status: str


class UndoResponse(BaseModel):
    id: str
    status: str  # 'reverted'
    op_type: str
    reverted: str  # human-readable description of what was reversed


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


# NOTE: The auto-approval rule CRUD models (AutoApprovalRule,
# *CreateRequest, *UpdateRequest) moved to the nuvrail-enterprise package along
# with the rules engine and the /rules API. The auto_approval_rules TABLE and
# the ExportAutoApprovalRule model below stay in core so account data-export
# keeps working in an open-core build (it returns an empty list there).
# See docs/REPO_SPLIT.md.


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
    undo_expires_at: Optional[int] = None  # null if not executed or undo window unknown
    # Semantic intent (gateway.intent): denormalized on audit_log, falls back
    # to the joined operation row for entries written before the column existed.
    intent_label: Optional[str] = None


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
    token: Optional[str] = None  # included on registration so client can auto-login


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
    upstream_smtp_host: Optional[str] = None  # defaults to upstream_host if not set (e.g. smtp.gmail.com)
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
    last_activity_at: Optional[int] = None  # most recent staged op; None = never connected


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



# ---------------------------------------------------------------------------
# Account maintenance models (issue #16)
# ---------------------------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    """Body for PUT /api/v1/auth/password."""
    current_password: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    ok: bool


class ResetRequestBody(BaseModel):
    """Body for POST /api/v1/auth/reset-request.

    Returns 200 regardless of whether the email is registered (no account
    enumeration). In Phase 2a, the reset URL is returned in the response so
    the admin can send it manually; in production it would be emailed.
    """
    email: str


class ResetRequestResponse(BaseModel):
    ok: bool


class ResetPasswordBody(BaseModel):
    """Body for POST /api/v1/auth/reset."""
    token: str
    new_password: str


class ResetPasswordResponse(BaseModel):
    ok: bool


class LogoutResponse(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Session / token management models (issue #28)
# ---------------------------------------------------------------------------


class TokenInfoResponse(BaseModel):
    """Response for GET /api/v1/account/token."""
    created_at: Optional[int] = None
    last_used_at: Optional[int] = None


class TokenRotateResponse(BaseModel):
    """Response for POST /api/v1/account/token/rotate — same shape as LoginResponse."""
    token: str
    token_type: str = "bearer"
    user_id: int
    email: str


# ---------------------------------------------------------------------------
# Data export models (issue #27)
# ---------------------------------------------------------------------------


class ExportAccount(BaseModel):
    email: str
    display_name: Optional[str] = None
    created_at: int


class ExportAgent(BaseModel):
    id: int
    label: str
    upstream_host: str
    upstream_user: str
    created_at: int
    revoked_at: Optional[int] = None


class ExportOperation(BaseModel):
    id: str
    created_at: int
    expires_at: int
    status: str
    op_type: str
    protocol: str
    description: str
    agent_id: Optional[str] = None
    decided_at: Optional[int] = None
    executed_at: Optional[int] = None
    error: Optional[str] = None


class ExportAuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: Optional[str] = None
    event: str
    actor: Optional[str] = None
    agent_id: Optional[str] = None
    op_type: Optional[str] = None
    detail: Optional[dict] = None
    # Hash-chain fields so the exported audit trail is independently verifiable.
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None

    @field_validator("detail", mode="before")
    @classmethod
    def parse_detail(cls, v: object) -> object:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:  # noqa: BLE001
                return None
        return v


class ExportMailboxMessage(BaseModel):
    """Message metadata held in the local mailbox mirror for this user.

    Envelope metadata only — Nuvrail does not store the body of messages the
    agent merely reads. Disclosed in the Privacy Policy; included here so the
    export reflects everything we hold about the user.
    """
    folder: str
    uid: int
    subject: Optional[str] = None
    sender: Optional[str] = None
    date_sent: Optional[int] = None
    flags: Optional[str] = None


class ExportPushSubscription(BaseModel):
    id: int
    endpoint: str
    created_at: int


class ExportAutoApprovalRule(BaseModel):
    id: int
    enabled: bool
    priority: int
    op_type: Optional[str] = None
    sender_pattern: Optional[str] = None
    folder_from: Optional[str] = None
    action: str
    description: str
    created_at: int


class DataExportResponse(BaseModel):
    exported_at: int
    account: ExportAccount
    agents: List[ExportAgent]
    operations: List[ExportOperation]
    audit_log: List[ExportAuditEntry]
    auto_approval_rules: List[ExportAutoApprovalRule]
    mailbox_messages: List[ExportMailboxMessage] = []
    push_subscriptions: List[ExportPushSubscription] = []
    # The audit-log chain head at export time, so the holder can later detect a
    # rewrite of the log up to this point.
    audit_chain_head: Optional[str] = None


# ---------------------------------------------------------------------------
# Account deletion model (issue #26)
# ---------------------------------------------------------------------------


class DeleteAccountRequest(BaseModel):
    """Body for DELETE /api/v1/account — password required for confirmation."""
    password: str


class DeleteAccountResponse(BaseModel):
    ok: bool
