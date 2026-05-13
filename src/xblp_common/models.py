"""SQLAlchemy ORM models and domain enums (see DESIGN.md §7)."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Domain enums ──────────────────────────────────────────────────────────────
# Stored as plain TEXT in SQLite; used by Pydantic schemas and application code
# for validation and type safety.


class Confidence(enum.StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class EventType(enum.StrEnum):
    rule_added = "rule_added"
    rule_removed = "rule_removed"
    rule_edited = "rule_edited"
    subscription_added = "subscription_added"
    subscription_removed = "subscription_removed"
    subscription_synced = "subscription_synced"
    profile_changed = "profile_changed"
    host_detected = "host_detected"
    login = "login"
    login_failed = "login_failed"
    logout = "logout"
    password_changed = "password_changed"
    config_changed = "config_changed"


class BlocklistFormat(enum.StrEnum):
    json = "json"
    txt = "txt"


# ── ORM models ────────────────────────────────────────────────────────────────
# Subscription is defined before Rule because Rule holds the FK.


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    format: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    refresh_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # passive_deletes=True: when a Subscription is deleted, SQLAlchemy skips
    # pre-loading children and lets the DB ON DELETE CASCADE do the work.
    rules: Mapped[list["Rule"]] = relationship(
        "Rule",
        back_populates="subscription",
        cascade="save-update, merge, delete",
        passive_deletes=True,
    )


class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("ip_address", "cidr_prefix", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip_address: Mapped[str] = mapped_column(String, nullable=False)
    cidr_prefix: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    # 'local' or 'subscription:<sub_id>'
    source: Mapped[str] = mapped_column(String, nullable=False)
    # Null for local rules; set to None on promotion (decouples from subscription)
    subscription_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored as string; see Confidence enum for valid values
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    subscription: Mapped[Subscription | None] = relationship("Subscription", back_populates="rules")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # See EventType enum for valid values
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # 'user' | 'system' | 'subscription:<id>'
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    undo_token: Mapped[str | None] = mapped_column(String, nullable=True)


class DetectedHost(Base):
    __tablename__ = "detected_hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ip_address: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    asn: Mapped[str | None] = mapped_column(String, nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    was_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ── Auth models (Phase 2) ─────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_username", "username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="save-update, merge, delete",
        passive_deletes=True,
    )


class UserSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    # 32 random bytes encoded as 64 hex characters; not autoincrement.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="sessions")
