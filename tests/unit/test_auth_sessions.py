"""Unit tests for server-side session management."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session as DbSession

from xblp_api.auth.sessions import (
    create_session,
    get_session,
    revoke_all_for_user,
    revoke_session,
    slide_session,
)
from xblp_api.config import Settings
from xblp_common.models import User

_SETTINGS = Settings(cookie_secure=False, nft_enabled=False, session_lifetime_days=30)


def _make_user(db: DbSession) -> User:
    user = User(
        username="testuser",
        password_hash="$argon2id$fake",
        must_change_password=False,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(user)
    db.flush()
    return user


@pytest.mark.unit
def test_create_session_returns_64_char_id(db_session):
    user = _make_user(db_session)
    sess = create_session(db_session, user.id, _SETTINGS)
    assert len(sess.id) == 64
    assert sess.user_id == user.id


@pytest.mark.unit
def test_get_session_returns_session(db_session):
    user = _make_user(db_session)
    sess = create_session(db_session, user.id, _SETTINGS)
    db_session.commit()

    found = get_session(db_session, sess.id)
    assert found is not None
    assert found.id == sess.id


@pytest.mark.unit
def test_get_session_unknown_id_returns_none(db_session):
    assert get_session(db_session, "nonexistent" * 4) is None


@pytest.mark.unit
def test_get_session_expired_returns_none(db_session):
    user = _make_user(db_session)
    sess = create_session(db_session, user.id, _SETTINGS)
    # Force expiry into the past
    sess.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    db_session.commit()

    assert get_session(db_session, sess.id) is None


@pytest.mark.unit
def test_slide_session_extends_expiry(db_session):
    user = _make_user(db_session)
    sess = create_session(db_session, user.id, _SETTINGS)
    original_expires = sess.expires_at
    db_session.commit()

    slide_session(db_session, sess, _SETTINGS)
    assert sess.expires_at >= original_expires


@pytest.mark.unit
def test_revoke_session_deletes_it(db_session):
    user = _make_user(db_session)
    sess = create_session(db_session, user.id, _SETTINGS)
    session_id = sess.id
    db_session.commit()

    revoke_session(db_session, sess)
    db_session.commit()

    assert get_session(db_session, session_id) is None


@pytest.mark.unit
def test_revoke_all_for_user_removes_all(db_session):
    user = _make_user(db_session)
    s1 = create_session(db_session, user.id, _SETTINGS)
    s2 = create_session(db_session, user.id, _SETTINGS)
    db_session.commit()

    count = revoke_all_for_user(db_session, user.id)
    db_session.commit()

    assert count == 2
    assert get_session(db_session, s1.id) is None
    assert get_session(db_session, s2.id) is None


@pytest.mark.unit
def test_revoke_all_except_current_session(db_session):
    user = _make_user(db_session)
    s1 = create_session(db_session, user.id, _SETTINGS)
    s2 = create_session(db_session, user.id, _SETTINGS)
    db_session.commit()

    count = revoke_all_for_user(db_session, user.id, except_session_id=s1.id)
    db_session.commit()

    assert count == 1
    assert get_session(db_session, s1.id) is not None
    assert get_session(db_session, s2.id) is None
