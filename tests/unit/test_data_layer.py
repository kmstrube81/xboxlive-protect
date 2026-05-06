"""Data layer tests: table creation, CRUD, constraints, cascade delete."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from xblp_common.models import AuditLog, DetectedHost, EventType, Rule, Subscription


def _now() -> datetime:
    """Naive UTC datetime for use with SQLite DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)


# ── Table creation ────────────────────────────────────────────────────────────


class TestTableCreation:
    def test_all_tables_exist(self, engine):  # type: ignore[no-untyped-def]
        tables = inspect(engine).get_table_names()
        assert "rules" in tables
        assert "subscriptions" in tables
        assert "audit_log" in tables
        assert "detected_hosts" in tables


# ── Subscription ──────────────────────────────────────────────────────────────


class TestSubscription:
    def test_insert_and_query(self, db_session: Session) -> None:
        sub = Subscription(
            name="Test List",
            url="https://example.com/list.json",
            format="json",
            created_at=_now(),
        )
        db_session.add(sub)
        db_session.commit()

        result = db_session.get(Subscription, sub.id)
        assert result is not None
        assert result.name == "Test List"
        assert result.enabled is True
        assert result.refresh_interval_hours == 24
        assert result.rule_count == 0
        assert result.last_fetched_at is None

    def test_url_unique_constraint(self, db_session: Session) -> None:
        url = "https://example.com/list.json"
        db_session.add(Subscription(name="A", url=url, format="json", created_at=_now()))
        db_session.commit()

        db_session.add(Subscription(name="B", url=url, format="json", created_at=_now()))
        with pytest.raises(IntegrityError):
            db_session.commit()


# ── Rule ──────────────────────────────────────────────────────────────────────


class TestRule:
    def test_insert_and_query(self, db_session: Session) -> None:
        now = _now()
        rule = Rule(ip_address="203.0.113.1", source="local", created_at=now, updated_at=now)
        db_session.add(rule)
        db_session.commit()

        result = db_session.get(Rule, rule.id)
        assert result is not None
        assert result.ip_address == "203.0.113.1"
        assert result.cidr_prefix == 32
        assert result.confidence is None
        assert result.subscription_id is None

    def test_unique_constraint_same_source(self, db_session: Session) -> None:
        now = _now()
        db_session.add(
            Rule(
                ip_address="1.2.3.4", cidr_prefix=32, source="local", created_at=now, updated_at=now
            )
        )
        db_session.commit()

        db_session.add(
            Rule(
                ip_address="1.2.3.4", cidr_prefix=32, source="local", created_at=now, updated_at=now
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_same_ip_different_source_allowed(self, db_session: Session) -> None:
        """Same IP/CIDR can appear once as local and once per subscription."""
        now = _now()
        sub = Subscription(
            name="Sub", url="https://example.com/x.json", format="json", created_at=now
        )
        db_session.add(sub)
        db_session.flush()

        db_session.add(Rule(ip_address="1.2.3.4", source="local", created_at=now, updated_at=now))
        db_session.add(
            Rule(
                ip_address="1.2.3.4",
                source=f"subscription:{sub.id}",
                subscription_id=sub.id,
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()

        rows = db_session.query(Rule).filter_by(ip_address="1.2.3.4").all()
        assert len(rows) == 2

    def test_cidr_uniqueness_is_per_prefix(self, db_session: Session) -> None:
        """Same IP with different CIDR prefixes are distinct rules."""
        now = _now()
        db_session.add(
            Rule(
                ip_address="10.0.0.0", cidr_prefix=8, source="local", created_at=now, updated_at=now
            )
        )
        db_session.add(
            Rule(
                ip_address="10.0.0.0",
                cidr_prefix=24,
                source="local",
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()

        rows = db_session.query(Rule).filter_by(ip_address="10.0.0.0").all()
        assert len(rows) == 2


# ── Cascade delete ────────────────────────────────────────────────────────────


class TestCascadeDelete:
    def test_deleting_subscription_removes_its_rules(self, db_session: Session) -> None:
        now = _now()
        sub = Subscription(
            name="Sub", url="https://example.com/s.json", format="json", created_at=now
        )
        db_session.add(sub)
        db_session.flush()

        rule = Rule(
            ip_address="5.5.5.5",
            source=f"subscription:{sub.id}",
            subscription_id=sub.id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(rule)
        db_session.commit()
        rule_id = rule.id

        db_session.delete(sub)
        db_session.commit()

        assert db_session.get(Rule, rule_id) is None

    def test_deleting_subscription_leaves_unrelated_local_rules(self, db_session: Session) -> None:
        now = _now()
        sub = Subscription(
            name="Sub2", url="https://example.com/s2.json", format="json", created_at=now
        )
        db_session.add(sub)
        db_session.flush()

        local_rule = Rule(ip_address="6.6.6.6", source="local", created_at=now, updated_at=now)
        db_session.add(local_rule)
        db_session.commit()
        local_id = local_rule.id

        db_session.delete(sub)
        db_session.commit()

        assert db_session.get(Rule, local_id) is not None

    def test_promoted_rule_survives_subscription_delete(self, db_session: Session) -> None:
        """Promoting a rule (source='local', subscription_id=None) detaches it from the
        subscription so it is retained when the subscription is later deleted."""
        now = _now()
        sub = Subscription(
            name="Sub3", url="https://example.com/s3.json", format="json", created_at=now
        )
        db_session.add(sub)
        db_session.flush()

        rule = Rule(
            ip_address="7.7.7.7",
            source=f"subscription:{sub.id}",
            subscription_id=sub.id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(rule)
        db_session.commit()
        rule_id = rule.id

        # Promote: detach from subscription
        rule.source = "local"
        rule.subscription_id = None
        db_session.commit()

        db_session.delete(sub)
        db_session.commit()

        assert db_session.get(Rule, rule_id) is not None


# ── AuditLog ──────────────────────────────────────────────────────────────────


class TestAuditLog:
    def test_insert_and_query(self, db_session: Session) -> None:
        entry = AuditLog(
            timestamp=_now(),
            event_type=EventType.rule_added,
            actor="user",
            target="203.0.113.1",
            details={"comment": "spinbot host", "cidr": 32},
            undo_token="tok_abc123",
        )
        db_session.add(entry)
        db_session.commit()

        result = db_session.get(AuditLog, entry.id)
        assert result is not None
        assert result.event_type == EventType.rule_added
        assert result.details == {"comment": "spinbot host", "cidr": 32}
        assert result.undo_token == "tok_abc123"

    def test_null_optional_fields(self, db_session: Session) -> None:
        entry = AuditLog(timestamp=_now(), event_type=EventType.login)
        db_session.add(entry)
        db_session.commit()

        result = db_session.get(AuditLog, entry.id)
        assert result is not None
        assert result.actor is None
        assert result.details is None
        assert result.undo_token is None


# ── DetectedHost ──────────────────────────────────────────────────────────────


class TestDetectedHost:
    def test_insert_and_query(self, db_session: Session) -> None:
        host = DetectedHost(
            detected_at=_now(),
            ip_address="203.0.113.99",
            profile_id="mw2-x360",
            score=450.0,
            duration_seconds=30,
            asn="AS12345",
            country_code="US",
        )
        db_session.add(host)
        db_session.commit()

        result = db_session.get(DetectedHost, host.id)
        assert result is not None
        assert result.score == 450.0
        assert result.was_blocked is False
        assert result.country_code == "US"
        assert result.asn == "AS12345"

    def test_was_blocked_default_false(self, db_session: Session) -> None:
        host = DetectedHost(
            detected_at=_now(),
            ip_address="198.51.100.1",
            profile_id="generic-p2p",
            score=120.0,
            duration_seconds=10,
        )
        db_session.add(host)
        db_session.commit()

        result = db_session.get(DetectedHost, host.id)
        assert result is not None
        assert result.was_blocked is False
        assert result.asn is None
        assert result.country_code is None
