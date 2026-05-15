"""Unit tests for TLS cert bootstrap (Windows-runnable, no root required).

Covers:
  - generate_self_signed_cert: file creation, cert content (CN, SAN, validity,
    key type/size), key/cert match, file modes (Linux only).
  - ensure_cert_exists: no-op when both files present; regenerates when either
    file is missing.
  - _ensure_tls_cert (app.py): skipped cleanly when tls_enabled=False.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from xblp_api.tls import (
    _CERT_CN,
    _CERT_SANS,
    _VALIDITY_DAYS,
    ensure_cert_exists,
    generate_self_signed_cert,
)


# ── generate_self_signed_cert ─────────────────────────────────────────────────


@pytest.mark.unit
def test_generate_writes_both_files(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)
    assert cert_path.exists()
    assert key_path.exists()


@pytest.mark.unit
def test_cert_cn(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(cert_path, tmp_path / "key.pem")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == _CERT_CN


@pytest.mark.unit
def test_cert_san(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(cert_path, tmp_path / "key.pem")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    assert set(dns_names) == set(_CERT_SANS)


@pytest.mark.unit
def test_cert_validity_days(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(cert_path, tmp_path / "key.pem")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert delta.days == _VALIDITY_DAYS


@pytest.mark.unit
def test_cert_is_rsa2048(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(cert_path, tmp_path / "key.pem")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    pub = cert.public_key()
    assert isinstance(pub, rsa.RSAPublicKey)
    assert pub.key_size == 2048


@pytest.mark.unit
def test_cert_key_match(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)

    # Round-trip sign+verify proves the private key matches the cert's public key.
    assert isinstance(key, rsa.RSAPrivateKey)
    message = b"xblp-tls-test"
    sig = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    cert.public_key().verify(sig, message, padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="file mode bits not meaningful on Windows")
def test_cert_file_modes(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)
    assert cert_path.stat().st_mode & 0o777 == 0o644
    assert key_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_generate_accepts_custom_cn_and_sans(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    generate_self_signed_cert(
        cert_path,
        tmp_path / "key.pem",
        cn="test-host",
        sans=["test-host.local"],
    )
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "test-host"
    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    assert dns_names == ["test-host.local"]


# ── ensure_cert_exists ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ensure_cert_exists_noop_when_both_present(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)

    mtime_cert = cert_path.stat().st_mtime_ns
    mtime_key = key_path.stat().st_mtime_ns

    ensure_cert_exists(cert_path, key_path)

    assert cert_path.stat().st_mtime_ns == mtime_cert
    assert key_path.stat().st_mtime_ns == mtime_key


@pytest.mark.unit
def test_ensure_cert_exists_regenerates_if_cert_missing(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)
    cert_path.unlink()

    ensure_cert_exists(cert_path, key_path)

    assert cert_path.exists()
    assert key_path.exists()


@pytest.mark.unit
def test_ensure_cert_exists_regenerates_if_key_missing(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)
    key_path.unlink()

    ensure_cert_exists(cert_path, key_path)

    assert cert_path.exists()
    assert key_path.exists()


@pytest.mark.unit
def test_ensure_cert_exists_creates_from_scratch(tmp_path: Path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    ensure_cert_exists(cert_path, key_path)

    assert cert_path.exists()
    assert key_path.exists()


# ── lifespan TLS skip on Windows ─────────────────────────────────────────────


@pytest.mark.unit
def test_ensure_tls_cert_skipped_when_disabled(tmp_path: Path) -> None:
    """_ensure_tls_cert does nothing when tls_enabled=False (Windows dev path)."""
    from xblp_api.app import _ensure_tls_cert
    from xblp_api.config import Settings

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    settings = Settings(
        tls_enabled=False,
        nft_enabled=False,
        cookie_secure=False,
        tls_cert_path=str(cert_path),
        tls_key_path=str(key_path),
    )
    _ensure_tls_cert(settings)

    assert not cert_path.exists()
    assert not key_path.exists()


@pytest.mark.unit
def test_ensure_tls_cert_generates_when_enabled(tmp_path: Path) -> None:
    """_ensure_tls_cert generates both files when tls_enabled=True."""
    from xblp_api.app import _ensure_tls_cert
    from xblp_api.config import Settings

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    settings = Settings(
        tls_enabled=True,
        nft_enabled=False,
        cookie_secure=False,
        tls_cert_path=str(cert_path),
        tls_key_path=str(key_path),
    )
    _ensure_tls_cert(settings)

    assert cert_path.exists()
    assert key_path.exists()
