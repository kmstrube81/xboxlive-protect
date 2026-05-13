"""Unit tests for password hashing (argon2id)."""

import pytest

from xblp_api.auth.hashing import hash_password, needs_rehash, verify_password
from xblp_api.config import Settings

_FAST_SETTINGS = Settings(
    cookie_secure=False,
    nft_enabled=False,
    argon2_time_cost=1,
    argon2_memory_cost=8192,
    argon2_parallelism=1,
)


@pytest.mark.unit
def test_hash_and_verify_roundtrip():
    h = hash_password("hunter2", _FAST_SETTINGS)
    assert verify_password(h, "hunter2", _FAST_SETTINGS)


@pytest.mark.unit
def test_wrong_password_returns_false():
    h = hash_password("correct", _FAST_SETTINGS)
    assert not verify_password(h, "wrong", _FAST_SETTINGS)


@pytest.mark.unit
def test_hash_is_not_plaintext():
    h = hash_password("secret", _FAST_SETTINGS)
    assert "secret" not in h
    assert h.startswith("$argon2")


@pytest.mark.unit
def test_needs_rehash_same_params():
    h = hash_password("pw", _FAST_SETTINGS)
    assert not needs_rehash(h, _FAST_SETTINGS)


@pytest.mark.unit
def test_needs_rehash_different_params():
    h = hash_password("pw", _FAST_SETTINGS)
    stronger = Settings(
        cookie_secure=False,
        nft_enabled=False,
        argon2_time_cost=3,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )
    assert needs_rehash(h, stronger)
