"""Pydantic v2 schemas for API request/response validation (see DESIGN.md §10)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xblp_common.models import BlocklistFormat, Confidence, EventType

# ── Subscription ──────────────────────────────────────────────────────────────


class SubscriptionCreate(BaseModel):
    """Request body for POST /subscriptions."""

    name: str
    url: str
    format: BlocklistFormat
    refresh_interval_hours: int = Field(default=24, ge=1)


class SubscriptionResponse(BaseModel):
    """Response shape for subscription endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    format: BlocklistFormat
    enabled: bool
    refresh_interval_hours: int
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    rule_count: int
    created_at: datetime


# ── Rule ──────────────────────────────────────────────────────────────────────


class RuleCreate(BaseModel):
    """Request body for POST /rules (user-created local rules only).

    source and subscription_id are not included here: local rules always have
    source='local' and subscription_id=None. Subscription sync code creates
    Rule ORM objects directly without going through this schema.
    """

    ip_address: str
    cidr_prefix: int = Field(default=32, ge=0, le=32)
    comment: str | None = None
    confidence: Confidence | None = None


class RuleUpdate(BaseModel):
    """Request body for PATCH /rules/{id} (local rules only)."""

    comment: str | None = None
    confidence: Confidence | None = None


class RuleResponse(BaseModel):
    """Response shape for rule endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ip_address: str
    cidr_prefix: int
    source: str
    subscription_id: int | None
    comment: str | None
    confidence: Confidence | None
    profile_id: str | None
    created_at: datetime
    updated_at: datetime


# ── AuditLog ──────────────────────────────────────────────────────────────────


class AuditLogEntry(BaseModel):
    """Response shape for audit log entries."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    event_type: EventType
    actor: str | None
    target: str | None
    details: dict[str, Any] | None
    undo_token: str | None


# ── DetectedHost ──────────────────────────────────────────────────────────────


class DetectedHostEntry(BaseModel):
    """Response shape for detected host log entries."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    detected_at: datetime
    ip_address: str
    profile_id: str
    score: float
    duration_seconds: int
    asn: str | None
    country_code: str | None
    was_blocked: bool
