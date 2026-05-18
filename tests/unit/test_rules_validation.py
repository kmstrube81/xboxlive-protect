"""Unit tests for xblp_common.validation.is_ip_blockable and helpers."""

from __future__ import annotations

import pytest

from xblp_common.validation import (
    get_default_gateway,
    get_bridge_ip,
    is_ip_blockable,
    load_xbl_allowlist,
)

pytestmark = pytest.mark.unit

# ── is_ip_blockable: basic rejection reasons ──────────────────────────────────


def test_public_ip_is_blockable():
    ok, reason = is_ip_blockable("203.0.113.45", gateway_ip=None, bridge_ip=None)
    assert ok is True
    assert reason is None


def test_invalid_ip_rejected():
    ok, reason = is_ip_blockable("not-an-ip", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "invalid"


def test_invalid_cidr_rejected():
    ok, reason = is_ip_blockable("203.0.113.1", 99, gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "invalid"


def test_loopback_rejected():
    ok, reason = is_ip_blockable("127.0.0.1", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "loopback"


def test_loopback_range_rejected():
    ok, reason = is_ip_blockable("127.10.20.30", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "loopback"


def test_private_10_rejected():
    ok, reason = is_ip_blockable("10.0.0.1", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "private"


def test_private_172_rejected():
    ok, reason = is_ip_blockable("172.20.0.1", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "private"


def test_private_192_168_rejected():
    ok, reason = is_ip_blockable("192.168.1.100", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "private"


def test_link_local_rejected():
    ok, reason = is_ip_blockable("169.254.1.1", gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "link-local"


# ── Subnet blocking (cidr_prefix != 32) ──────────────────────────────────────


def test_public_cidr_is_blockable():
    ok, reason = is_ip_blockable("203.0.113.0", 24, gateway_ip=None, bridge_ip=None)
    assert ok is True
    assert reason is None


def test_cidr_overlapping_private_rejected():
    ok, reason = is_ip_blockable("10.0.0.0", 8, gateway_ip=None, bridge_ip=None)
    assert ok is False
    assert reason == "private"


# ── Xbox Live allowlist injection ─────────────────────────────────────────────


def test_xbl_allowlist_blocks_overlapping_ip():
    allowlist = [("198.51.100.0", 24)]
    ok, reason = is_ip_blockable(
        "198.51.100.45", xbl_allowlist=allowlist, gateway_ip=None, bridge_ip=None
    )
    assert ok is False
    assert reason == "xbox-live"


def test_xbl_allowlist_does_not_block_non_overlapping():
    allowlist = [("198.51.100.0", 24)]
    ok, reason = is_ip_blockable(
        "198.51.101.1", xbl_allowlist=allowlist, gateway_ip=None, bridge_ip=None
    )
    assert ok is True


def test_xbl_allowlist_empty_list_skips_check():
    ok, reason = is_ip_blockable("203.0.113.1", xbl_allowlist=[], gateway_ip=None, bridge_ip=None)
    assert ok is True


def test_xbl_allowlist_bad_entry_skipped_gracefully():
    allowlist = [("not-a-real-ip", 32), ("203.0.113.0", 24)]
    ok, reason = is_ip_blockable(
        "203.0.113.5", xbl_allowlist=allowlist, gateway_ip=None, bridge_ip=None
    )
    assert ok is False
    assert reason == "xbox-live"


# ── Gateway and bridge injection ──────────────────────────────────────────────


def test_gateway_ip_rejected():
    # Use a public IP as the "gateway" so it isn't caught by private first.
    ok, reason = is_ip_blockable(
        "203.0.113.1", gateway_ip="203.0.113.1", bridge_ip=None
    )
    assert ok is False
    assert reason == "gateway"


def test_bridge_ip_rejected():
    ok, reason = is_ip_blockable(
        "203.0.113.2", gateway_ip=None, bridge_ip="203.0.113.2"
    )
    assert ok is False
    assert reason == "bridge"


def test_gateway_none_skips_check():
    ok, reason = is_ip_blockable("203.0.113.1", gateway_ip=None, bridge_ip=None)
    assert ok is True


def test_bridge_none_skips_check():
    ok, reason = is_ip_blockable("203.0.113.1", gateway_ip=None, bridge_ip=None)
    assert ok is True


def test_different_ip_not_rejected_as_gateway():
    ok, reason = is_ip_blockable(
        "203.0.113.45", gateway_ip="203.0.113.1", bridge_ip=None
    )
    assert ok is True


# ── Check order: private before gateway ──────────────────────────────────────


def test_private_caught_before_gateway():
    # A private IP that also matches the gateway should be caught as 'private'.
    ok, reason = is_ip_blockable(
        "192.168.1.1", gateway_ip="192.168.1.1", bridge_ip=None
    )
    assert ok is False
    assert reason == "private"


# ── load_xbl_allowlist: missing file returns [] ───────────────────────────────


def test_load_xbl_allowlist_missing_file_returns_empty(tmp_path, monkeypatch):
    import xblp_common.validation as val_mod

    monkeypatch.setattr(val_mod, "_XBL_ALLOWLIST_PATH", tmp_path / "does_not_exist.json")
    result = val_mod.load_xbl_allowlist()
    assert result == []


def test_load_xbl_allowlist_parses_entries(tmp_path, monkeypatch):
    import json
    import xblp_common.validation as val_mod

    data = {
        "entries": [
            {"ip": "198.51.100.0", "cidr": 24},
            {"ip": "203.0.113.1", "cidr": 32},
        ]
    }
    p = tmp_path / "allowlist.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(val_mod, "_XBL_ALLOWLIST_PATH", p)
    result = val_mod.load_xbl_allowlist()
    assert ("198.51.100.0", 24) in result
    assert ("203.0.113.1", 32) in result


# ── get_default_gateway / get_bridge_ip: graceful on Windows ─────────────────


def test_get_default_gateway_returns_none_or_string():
    result = get_default_gateway()
    assert result is None or isinstance(result, str)


def test_get_bridge_ip_returns_none_or_string():
    result = get_bridge_ip()
    assert result is None or isinstance(result, str)
