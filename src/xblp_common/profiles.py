"""Game profile schema and loader (see DESIGN.md §5)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, field_validator, model_validator

log = structlog.get_logger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_VALID_CONFIDENCE = frozenset({"experimental", "community-validated", "tested"})


class ProfileLoadError(Exception):
    """Raised when a profile file cannot be parsed or fails validation."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path


class PortRange(BaseModel):
    min: int
    max: int

    @model_validator(mode="after")
    def _check_range(self) -> PortRange:
        if not (1 <= self.min < self.max <= 65535):
            raise ValueError(
                f"port range must satisfy 1 ≤ min < max ≤ 65535, got min={self.min} max={self.max}"
            )
        return self


class DetectionConfig(BaseModel):
    transport: str
    port_ranges: list[PortRange]
    min_pps: float
    window_seconds: int
    min_consecutive_windows: int

    @field_validator("min_pps")
    @classmethod
    def _positive_pps(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"min_pps must be > 0, got {v}")
        return v

    @field_validator("window_seconds")
    @classmethod
    def _positive_window(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"window_seconds must be > 0, got {v}")
        return v

    @field_validator("min_consecutive_windows")
    @classmethod
    def _min_one_window(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"min_consecutive_windows must be ≥ 1, got {v}")
        return v


class Profile(BaseModel):
    id: str
    name: str
    console: str
    confidence: str
    maintainer: str
    last_validated: date
    description: str | None = None
    detection: DetectionConfig
    exclude_ranges: list[str] = []
    payload_signatures: list[Any] = []

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                f"profile id must match ^[a-z][a-z0-9-]*$, got {v!r}"
            )
        return v

    @field_validator("confidence")
    @classmethod
    def _valid_confidence(cls, v: str) -> str:
        if v not in _VALID_CONFIDENCE:
            raise ValueError(
                f"confidence must be one of {sorted(_VALID_CONFIDENCE)}, got {v!r}"
            )
        return v


def load_profile(path: Path) -> Profile:
    """Load and validate a single profile YAML file.

    Always uses yaml.safe_load — never yaml.load — so community-contributed
    profiles cannot execute arbitrary Python via YAML tags.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileLoadError(f"YAML parse error: {exc}", path) from exc
    except OSError as exc:
        raise ProfileLoadError(f"cannot read file: {exc}", path) from exc

    if not isinstance(raw, dict):
        raise ProfileLoadError("YAML root must be a mapping", path)

    try:
        profile = Profile.model_validate(raw)
    except Exception as exc:
        raise ProfileLoadError(f"validation error: {exc}", path) from exc

    log.debug("profile loaded", profile_id=profile.id, path=str(path))
    return profile


def discover_profiles(directory: Path) -> dict[str, Profile]:
    """Load all *.yaml files from *directory* and return them keyed by profile id.

    Raises ProfileLoadError immediately if any file is invalid — profiles are
    never silently skipped, so a broken community profile is always visible.
    """
    profiles: dict[str, Profile] = {}
    yaml_files = sorted(directory.glob("*.yaml"))

    for yaml_path in yaml_files:
        profile = load_profile(yaml_path)  # propagates ProfileLoadError on failure
        profiles[profile.id] = profile
        log.info("profile registered", profile_id=profile.id)

    log.info("profiles discovered", count=len(profiles), directory=str(directory))
    return profiles
