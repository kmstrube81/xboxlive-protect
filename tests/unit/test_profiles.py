"""Unit tests for xblp_common.profiles — schema, loader, and discovery."""

import re
from pathlib import Path

import pytest
import yaml

from xblp_common.profiles import Profile, ProfileLoadError, discover_profiles, load_profile

# Path to the checked-in profiles directory (relative to repo root)
PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"

# ── Helpers ───────────────────────────────────────────────────────────────────

_BASE = {
    "id": "test-game",
    "name": "Test Game",
    "console": "xbox-360",
    "confidence": "tested",
    "maintainer": "tester",
    "last_validated": "2026-05-06",
    "detection": {
        "transport": "udp",
        "port_ranges": [{"min": 1000, "max": 65535}],
        "min_pps": 30,
        "window_seconds": 10,
        "min_consecutive_windows": 3,
    },
}


def _write_yaml(tmp_path: Path, data: object, filename: str = "profile.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _bad_profile(tmp_path: Path, overrides: dict) -> Path:
    data = {**_BASE, **overrides}
    return _write_yaml(tmp_path, data)


# ── Built-in profiles ─────────────────────────────────────────────────────────


def test_all_builtin_profiles_load():
    profiles = discover_profiles(PROFILES_DIR)
    expected = {"mw2-x360", "halo3-x360", "halo-reach-x360", "generic-p2p", "monitoring-only"}
    assert set(profiles.keys()) == expected


def test_builtin_profiles_are_profile_instances():
    profiles = discover_profiles(PROFILES_DIR)
    for profile in profiles.values():
        assert isinstance(profile, Profile)


def test_monitoring_only_has_inert_detection():
    profiles = discover_profiles(PROFILES_DIR)
    mon = profiles["monitoring-only"]
    assert mon.detection.min_pps >= 999999


# ── id validation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "MW2-x360",  # uppercase
        "mw2 x360",  # space
        "123-game",  # starts with digit
        "-mw2",  # starts with hyphen
        "mw2_x360",  # underscore
        "",  # empty
    ],
)
def test_invalid_id_format_raises(tmp_path, bad_id):
    path = _bad_profile(tmp_path, {"id": bad_id})
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


# ── confidence validation ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_confidence", ["unknown", "high", "medium", "low", "TESTED", ""])
def test_invalid_confidence_raises(tmp_path, bad_confidence):
    path = _bad_profile(tmp_path, {"confidence": bad_confidence})
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


# ── PortRange validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_range",
    [
        {"min": 500, "max": 500},  # min == max
        {"min": 600, "max": 400},  # min > max
    ],
)
def test_port_range_min_gte_max_raises(tmp_path, bad_range):
    data = {**_BASE, "detection": {**_BASE["detection"], "port_ranges": [bad_range]}}
    path = _write_yaml(tmp_path, data)
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


@pytest.mark.parametrize(
    "bad_range",
    [
        {"min": 0, "max": 1000},  # min below 1
        {"min": 1000, "max": 70000},  # max above 65535
        {"min": 0, "max": 70000},  # both out of bounds
    ],
)
def test_port_range_out_of_bounds_raises(tmp_path, bad_range):
    data = {**_BASE, "detection": {**_BASE["detection"], "port_ranges": [bad_range]}}
    path = _write_yaml(tmp_path, data)
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


# ── Detection numeric validation ──────────────────────────────────────────────


@pytest.mark.parametrize("bad_pps", [0, -1, -100])
def test_non_positive_min_pps_raises(tmp_path, bad_pps):
    data = {**_BASE, "detection": {**_BASE["detection"], "min_pps": bad_pps}}
    path = _write_yaml(tmp_path, data)
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


@pytest.mark.parametrize("bad_window", [0, -1])
def test_non_positive_window_seconds_raises(tmp_path, bad_window):
    data = {**_BASE, "detection": {**_BASE["detection"], "window_seconds": bad_window}}
    path = _write_yaml(tmp_path, data)
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


@pytest.mark.parametrize("bad_consecutive", [0, -1])
def test_min_consecutive_windows_below_one_raises(tmp_path, bad_consecutive):
    data = {
        **_BASE,
        "detection": {**_BASE["detection"], "min_consecutive_windows": bad_consecutive},
    }
    path = _write_yaml(tmp_path, data)
    with pytest.raises(ProfileLoadError, match=re.escape(str(path))):
        load_profile(path)


# ── Error messages include file path ──────────────────────────────────────────


def test_load_profile_error_includes_path(tmp_path):
    path = _bad_profile(tmp_path, {"id": "INVALID"})
    with pytest.raises(ProfileLoadError) as exc_info:
        load_profile(path)
    assert str(path) in str(exc_info.value)


def test_load_profile_bad_yaml_includes_path(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("key: [\nunclosed bracket", encoding="utf-8")
    with pytest.raises(ProfileLoadError) as exc_info:
        load_profile(path)
    assert str(path) in str(exc_info.value)


def test_load_profile_non_mapping_yaml_includes_path(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ProfileLoadError) as exc_info:
        load_profile(path)
    assert str(path) in str(exc_info.value)


# ── safe_load enforcement ─────────────────────────────────────────────────────


def test_safe_load_blocks_python_object_tags(tmp_path):
    """A YAML file with !!python/object tags must raise, not execute Python."""
    malicious = "!!python/object/apply:os.system ['echo pwned']\n"
    path = tmp_path / "malicious.yaml"
    path.write_text(malicious, encoding="utf-8")
    # yaml.safe_load raises yaml.constructor.ConstructorError on unknown tags;
    # load_profile must surface this as ProfileLoadError, never execute the payload.
    with pytest.raises(ProfileLoadError):
        load_profile(path)


# ── discover_profiles edge cases ──────────────────────────────────────────────


def test_discover_profiles_empty_directory(tmp_path):
    result = discover_profiles(tmp_path)
    assert result == {}


def test_discover_profiles_raises_on_invalid_file(tmp_path):
    """A single broken YAML must cause discover_profiles to raise, not silently skip."""
    good = {**_BASE}
    _write_yaml(tmp_path, good, "good.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: INVALID_ID\nname: Bad\n", encoding="utf-8")
    with pytest.raises(ProfileLoadError):
        discover_profiles(tmp_path)


def test_discover_profiles_ignores_non_yaml_files(tmp_path):
    """Non-.yaml files in the directory are not loaded."""
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "profile.json").write_text("{}", encoding="utf-8")
    result = discover_profiles(tmp_path)
    assert result == {}
