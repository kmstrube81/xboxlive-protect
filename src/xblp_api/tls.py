"""TLS bootstrap for xblp-api (see DESIGN.md §6.1).

Generates a self-signed RSA-2048 certificate at first daemon start. Files are
written atomically via tempfile-then-rename with the following permissions:
  cert.pem — 0644  (nginx must be able to read it)
  key.pem  — 0600  (daemon user only)

Certificate profile:
  CN       = "xboxlive-protect"
  SAN      = DNS:xboxlive-protect.local, DNS:xboxlive-protect
  Key      = RSA-2048 (chosen over ECDSA P-256 for compatibility with legacy
             TLS stacks in the retro Xbox-adjacent ecosystem)
  Validity = 10 years — no rotation logic; appliance use case per §6.1
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

log = structlog.get_logger(__name__)

_CERT_CN = "xboxlive-protect"
_CERT_SANS: tuple[str, ...] = ("xboxlive-protect.local", "xboxlive-protect")
_VALIDITY_DAYS = 3650  # 10 years


def generate_self_signed_cert(
    cert_path: Path,
    key_path: Path,
    cn: str = _CERT_CN,
    sans: list[str] | None = None,
) -> None:
    """Write a self-signed RSA-2048 cert and private key.

    Files are written to temp files first, then renamed into place atomically.
    The key temp file is chmod'd 0600 before rename so it is never world-readable,
    even briefly.
    """
    _sans = list(_CERT_SANS) if sans is None else sans

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san) for san in _sans]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    _atomic_write(cert_path, cert_pem, mode=0o644)
    _atomic_write(key_path, key_pem, mode=0o600)


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def ensure_cert_exists(cert_path: Path, key_path: Path) -> None:
    """Generate cert+key if either file is absent; no-op if both exist."""
    if cert_path.exists() and key_path.exists():
        log.debug("tls cert already present, skipping generation", cert=str(cert_path))
        return
    log.info("generating self-signed tls cert", cert=str(cert_path), key=str(key_path))
    generate_self_signed_cert(cert_path, key_path)
    log.info(
        "tls cert generated",
        cn=_CERT_CN,
        sans=list(_CERT_SANS),
        validity_days=_VALIDITY_DAYS,
    )
