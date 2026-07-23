"""
Pydantic response models for the Approval API.

Sub-milestone: 1.0
"""
from __future__ import annotations

import json

from pydantic import BaseModel, field_validator


class MessagePreview(BaseModel):
    """Sender and subject for a single message affected by an IMAP operation."""
    uid: int
    sender: str | None = None
    subject: str | None = None
    date_sent: int | None = None


class OperationResponse(BaseModel):
    id: str
    status: str
    op_type: str
    protocol: str
    description: str
    created_at: int
    expires_at: int
    decided_at: int | None = None
    scheduled_execute_at: int | None = None  # cool-down deadline (approve_after)
    imap_command: str | None = None
    smtp_envelope: dict | None = None
    message_ids: list[str] = []
    folder_from: str | None = None
    folder_to: str | None = None
    flags_add: list[str] = []
    flags_remove: list[str] = []
    is_urgent: int = 0
    error: str | None = None
    message_previews: list[MessagePreview] = []
    batch_id: str | None = None
    # Semantic intent derived at staging time (gateway.intent): 'archive',
    # 'delete', 'mark_spam', ... None = the op_type already says everything.
    intent_label: str | None = None
    intent_confidence: float | None = None

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
    operations: list[OperationResponse]
    total: int
    # batch_id → one-line human summary, for batches with 2+ operations in
    # this response (gateway.batch_summary). The UI shows it as the batch
    # card's header instead of "Batch — N operations".
    batch_summaries: dict[str, str] = {}


class ApproveResponse(BaseModel):
    id: str
    status: str
    executed_at: int | None = None
    error: str | None = None


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
    operation_ids: list[str]


class BatchRejectRequest(BaseModel):
    operation_ids: list[str]


class BatchApproveResult(BaseModel):
    id: str
    status: str  # 'executed' | 'failed' | 'skipped'
    executed_at: int | None = None
    error: str | None = None  # set if status='failed'


class BatchRejectResult(BaseModel):
    id: str
    status: str  # 'rejected' | 'failed' | 'skipped'
    error: str | None = None


class BatchApproveResponse(BaseModel):
    approved: list[BatchApproveResult]
    failed: list[BatchApproveResult]
    skipped: list[BatchApproveResult]  # not pending (already decided) or not found
    total: int


class BatchRejectResponse(BaseModel):
    rejected: list[BatchRejectResult]
    failed: list[BatchRejectResult]
    skipped: list[BatchRejectResult]  # not pending (already decided) or not found
    total: int


# NOTE: The auto-approval rule CRUD models (AutoApprovalRule,
# *CreateRequest, *UpdateRequest) moved to the nuvrail-enterprise package along
# with the rules engine and the /rules API. The auto_approval_rules TABLE and
# the ExportAutoApprovalRule model below stay in core so account data-export
# keeps working in an open-core build (it returns an empty list there).


class AuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: str | None = None
    event: str
    actor: str | None = None
    agent_id: str | None = None
    agent_label: str | None = None
    detail: dict | None = None       # parsed JSON from audit_log.detail
    # Joined from staged_operations (None if operation record no longer exists)
    op_description: str | None = None
    op_type: str | None = None
    op_protocol: str | None = None
    op_status: str | None = None
    undo_expires_at: int | None = None  # null if not executed or undo window unknown
    # Semantic intent (gateway.intent): denormalized on audit_log, falls back
    # to the joined operation row for entries written before the column existed.
    intent_label: str | None = None


class AuditListResponse(BaseModel):
    entries: list[AuditEntry]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Auth + Agent credential models (Lane 2 / Lane 3)
# ---------------------------------------------------------------------------


class UserCreateRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    # Required only when the deployment runs NUVRAIL_SIGNUP_MODE=invite;
    # ignored in open mode and rejected (as unnecessary) is not enforced.
    invite_code: str | None = None


class UserResponse(BaseModel):
    user_id: int
    email: str
    display_name: str | None = None
    created_at: int
    token: str | None = None  # included on registration so client can auto-login


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token_type field name, not a secret
    user_id: int
    email: str


class AgentCreateRequest(BaseModel):
    label: str | None = "default"
    upstream_host: str
    upstream_smtp_host: str | None = None  # defaults to upstream_host if not set (e.g. smtp.gmail.com)
    upstream_imap_port: int = 993
    upstream_smtp_port: int = 587
    upstream_user: str
    # Password auth (mutually exclusive with OAuth2 fields below)
    upstream_password: str | None = None
    # OAuth2 / XOAUTH2 fields (all required together if oauth2_provider is set)
    oauth2_provider: str | None = None          # "google" | "microsoft"
    oauth2_client_id: str | None = None
    oauth2_client_secret: str | None = None
    oauth2_refresh_token: str | None = None


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
    revoked_at: int | None = None
    last_activity_at: int | None = None  # most recent staged op; None = never connected


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
    created_at: int | None = None
    last_used_at: int | None = None


class TokenRotateResponse(BaseModel):
    """Response for POST /api/v1/account/token/rotate — same shape as LoginResponse."""
    token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token_type field name, not a secret
    user_id: int
    email: str


# ---------------------------------------------------------------------------
# Data export models (issue #27)
# ---------------------------------------------------------------------------


class ExportAccount(BaseModel):
    email: str
    display_name: str | None = None
    created_at: int


class ExportAgent(BaseModel):
    id: int
    label: str
    upstream_host: str
    upstream_user: str
    created_at: int
    revoked_at: int | None = None


class ExportOperation(BaseModel):
    id: str
    created_at: int
    expires_at: int
    status: str
    op_type: str
    protocol: str
    description: str
    agent_id: str | None = None
    decided_at: int | None = None
    executed_at: int | None = None
    error: str | None = None


class ExportAuditEntry(BaseModel):
    id: int
    timestamp: int
    operation_id: str | None = None
    event: str
    actor: str | None = None
    agent_id: str | None = None
    op_type: str | None = None
    detail: dict | None = None
    # Hash-chain fields so the exported audit trail is independently verifiable.
    prev_hash: str | None = None
    entry_hash: str | None = None

    @field_validator("detail", mode="before")
    @classmethod
    def parse_detail(cls, v: object) -> object:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
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
    subject: str | None = None
    sender: str | None = None
    date_sent: int | None = None
    flags: str | None = None


class ExportPushSubscription(BaseModel):
    id: int
    endpoint: str
    created_at: int


class ExportAutoApprovalRule(BaseModel):
    id: int
    enabled: bool
    priority: int
    op_type: str | None = None
    sender_pattern: str | None = None
    folder_from: str | None = None
    action: str
    description: str
    created_at: int


class DataExportResponse(BaseModel):
    exported_at: int
    account: ExportAccount
    agents: list[ExportAgent]
    operations: list[ExportOperation]
    audit_log: list[ExportAuditEntry]
    auto_approval_rules: list[ExportAutoApprovalRule]
    mailbox_messages: list[ExportMailboxMessage] = []
    push_subscriptions: list[ExportPushSubscription] = []
    # The audit-log chain head at export time, so the holder can later detect a
    # rewrite of the log up to this point.
    audit_chain_head: str | None = None


# ---------------------------------------------------------------------------
# Account deletion model (issue #26)
# ---------------------------------------------------------------------------


class DeleteAccountRequest(BaseModel):
    """Body for DELETE /api/v1/account — password required for confirmation."""
    password: str


class DeleteAccountResponse(BaseModel):
    ok: bool
