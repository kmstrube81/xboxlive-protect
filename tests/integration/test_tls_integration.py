"""Integration tests for TLS cert bootstrap and nginx chain (run with: pytest -m integration).

Test 1 — cert generation on a real filesystem:
  Verifies cert.pem + key.pem are written with correct modes and the cert
  validates cryptographically. Runs on Linux only; no root required (uses tmp_path).

Test 2 — nginx config syntax:
  Runs 'nginx -t' with a patched config pointing at a freshly-generated cert.
  Skipped if nginx is not installed.

Tests 3–4 — full HTTP/HTTPS chain (requires running stack):
  Curl probes against https://xboxlive-protect.local. Skipped if the host
  does not resolve. These correspond to exit criteria 1–2 in the Stage 1 TLS
  spec and are intended for manual verification after running install-stage1.sh,
  but are expressed as skippable pytest cases for CI on the R4S.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ── Test 1: cert on real filesystem ──────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.linux
def test_cert_written_with_correct_content_and_modes(tmp_path: Path) -> None:
    """generate_self_signed_cert produces a valid cert+key pair with correct modes."""
    from xblp_api.tls import _CERT_CN, _CERT_SANS, _VALIDITY_DAYS, generate_self_signed_cert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"

    generate_self_signed_cert(cert_path, key_path)

    # Modes
    assert cert_path.stat().st_mode & 0o777 == 0o644
    assert key_path.stat().st_mode & 0o777 == 0o600

    # Cert content
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == _CERT_CN

    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    assert set(dns_names) == set(_CERT_SANS)

    delta = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert delta.days == _VALIDITY_DAYS

    # Key/cert match
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.key_size == 2048

    message = b"xblp-integration-test"
    sig = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    cert.public_key().verify(sig, message, padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.integration
@pytest.mark.linux
def test_ensure_cert_exists_idempotent_on_restart(tmp_path: Path) -> None:
    """ensure_cert_exists does not regenerate when both files already exist."""
    from xblp_api.tls import ensure_cert_exists, generate_self_signed_cert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)

    mtime_cert = cert_path.stat().st_mtime_ns
    mtime_key = key_path.stat().st_mtime_ns

    # Simulate daemon restart
    ensure_cert_exists(cert_path, key_path)

    assert cert_path.stat().st_mtime_ns == mtime_cert
    assert key_path.stat().st_mtime_ns == mtime_key


# ── Test 2: nginx config syntax ───────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.linux
def test_nginx_config_syntax(tmp_path: Path) -> None:
    """nginx -t passes against xblp.conf when cert files are in place."""
    if not shutil.which("nginx"):
        pytest.skip("nginx not installed")

    from xblp_api.tls import generate_self_signed_cert

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    generate_self_signed_cert(cert_path, key_path)

    # Write a patched copy of the nginx config pointing at our temp cert paths.
    repo_conf = Path(__file__).parents[2] / "deploy" / "nginx" / "xblp.conf"
    patched_conf = tmp_path / "xblp-test.conf"
    content = repo_conf.read_text()
    content = content.replace(
        "/var/lib/xboxlive-protect/cert.pem", str(cert_path)
    ).replace(
        "/var/lib/xboxlive-protect/key.pem", str(key_path)
    )
    patched_conf.write_text(content)

    # nginx -t needs a minimal nginx.conf that includes our site conf.
    nginx_conf = tmp_path / "nginx.conf"
    nginx_conf.write_text(
        f"events {{}}\n"
        f"http {{\n"
        f"    include {patched_conf};\n"
        f"}}\n"
    )

    result = subprocess.run(
        ["nginx", "-t", "-c", str(nginx_conf)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"nginx -t failed:\n{result.stderr}"


# ── Tests 3–4: full HTTP/HTTPS chain (requires running stack) ─────────────────


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port can be established."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.integration
@pytest.mark.linux
def test_https_chain_returns_401(tmp_path: Path) -> None:
    """HTTPS request to /api/v1/auth/me returns 401 through the nginx+daemon chain.

    Requires: install-stage1.sh has been run and nginx is listening on 443.
    """
    if not shutil.which("curl"):
        pytest.skip("curl not installed")
    if not _port_open("xboxlive-protect.local", 443):
        pytest.skip("nginx not listening on 443 — run deploy/install-stage1.sh first")

    result = subprocess.run(
        [
            "curl", "-k", "-s", "-o", "/dev/null",
            "-w", "%{http_code}",
            "https://xboxlive-protect.local/api/v1/auth/me",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "401", (
        f"Expected 401, got {result.stdout.strip()!r}. "
        "Is xblp-api running and nginx proxying correctly?"
    )


@pytest.mark.integration
@pytest.mark.linux
def test_http_redirects_to_https(tmp_path: Path) -> None:
    """HTTP request to port 80 returns 301 redirect to https://.

    Requires: install-stage1.sh has been run and nginx is listening on 80.
    """
    if not shutil.which("curl"):
        pytest.skip("curl not installed")
    if not _port_open("xboxlive-protect.local", 80):
        pytest.skip("nginx not listening on 80 — run deploy/install-stage1.sh first")

    result = subprocess.run(
        [
            "curl", "-s", "-o", "/dev/null",
            "-w", "%{http_code}:%{redirect_url}",
            "http://xboxlive-protect.local/api/v1/auth/me",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    http_code, redirect_url = result.stdout.strip().split(":", 1)
    assert http_code == "301", f"Expected 301, got {http_code!r}"
    assert redirect_url.startswith("https://"), (
        f"Expected redirect to https://, got {redirect_url!r}"
    )
