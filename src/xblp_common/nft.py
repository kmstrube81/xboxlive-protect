"""nftables management wrapper (see DESIGN.md §4.3).

All nftables operations are executed via the nft(8) CLI as a subprocess.
Python libnftables bindings are not used — packaging on ARM64 Debian is
a dependency footgun that isn't worth it.

Set-element operations use ``nft -f <tmpfile>`` rather than bare argv to
avoid shell-quoting ambiguity with ``{ }`` set literals when called via
subprocess without a shell.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_NFT_BIN = "/usr/sbin/nft"
_DEFAULT_TABLE = "xblp"
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "deploy" / "nftables" / "xblp.nft.template"
)


class NftError(Exception):
    """Raised when an nft subprocess invocation fails."""


class NftManager:
    """Manages the xblp nftables table, sets, and forward chain.

    Pass a custom ``table`` name to target a different table — the integration
    test suite uses ``xblp_test`` to avoid clobbering a production install.

    The ``nft_bin`` path defaults to the ``XBLP_NFT_BIN`` environment variable,
    then ``/usr/sbin/nft``. An explicit constructor argument takes precedence
    over the environment variable.
    """

    def __init__(
        self,
        nft_bin: str | None = None,
        table: str = _DEFAULT_TABLE,
        template_path: Path | None = None,
    ) -> None:
        self.nft_bin: str = nft_bin or os.environ.get("XBLP_NFT_BIN", _DEFAULT_NFT_BIN)
        self.table: str = table
        self._template_path: Path = template_path or _TEMPLATE_PATH
        self._log = log.bind(table=table, nft_bin=self.nft_bin)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run(self, args: list[str]) -> str:
        """Invoke ``nft <args>``. Returns stdout. Raises :exc:`NftError` on failure."""
        cmd = [self.nft_bin, *args]
        self._log.debug("nft", cmd=cmd)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise NftError(
                f"`nft {' '.join(args)}` exited {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout

    def _run_script(self, content: str) -> None:
        """Write *content* to a temp ``.nft`` file and apply it via ``nft -f``."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".nft", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = f.name
        try:
            self._run(["-f", tmp_path])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # ── Ruleset lifecycle ─────────────────────────────────────────────────────

    def apply_initial_ruleset(self) -> None:
        """Apply the table/sets/chain from the template. No-op if already present.

        Idempotent: calling twice is safe — the second call detects the table
        exists and returns without touching nftables.
        """
        if self.verify_ruleset_present():
            self._log.debug("ruleset already present, skipping apply")
            return
        template = self._template_path.read_text(encoding="utf-8")
        ruleset = template.replace("{table}", self.table)
        self._run_script(ruleset)
        self._log.info("initial ruleset applied")

    def verify_ruleset_present(self) -> bool:
        """Return ``True`` if our table and forward chain exist in nftables."""
        try:
            self._run(["list", "chain", "inet", self.table, "forward"])
            return True
        except NftError:
            return False

    def remove_ruleset(self) -> None:
        """Delete the entire table. Used for clean uninstall."""
        self._run(["delete", "table", "inet", self.table])
        self._log.info("ruleset removed")

    # ── Blocklist ─────────────────────────────────────────────────────────────

    def add_to_blocklist(self, ip: str, cidr: int = 32) -> None:
        self._run_script(f"add element inet {self.table} blocklist {{ {ip}/{cidr} }}\n")
        self._log.info("blocklist add", ip=ip, cidr=cidr)

    def remove_from_blocklist(self, ip: str, cidr: int = 32) -> None:
        self._run_script(f"delete element inet {self.table} blocklist {{ {ip}/{cidr} }}\n")
        self._log.info("blocklist remove", ip=ip, cidr=cidr)

    def list_blocklist(self) -> list[tuple[str, int]]:
        output = self._run(["list", "set", "inet", self.table, "blocklist"])
        return _parse_set_elements(output)

    def apply_diff(
        self,
        table_set: str,
        to_add: list[tuple[str, int]],
        to_remove: list[tuple[str, int]],
    ) -> None:
        """Apply a set diff as a single atomic ``nft -f`` transaction.

        Removes are written before adds so that a broader-to-narrower transition
        (e.g. /24 → /32) succeeds: the /24 must leave the set before the /32
        can be inserted without triggering the kernel's interval-overlap check.
        No-op if both lists are empty.
        """
        if not to_add and not to_remove:
            return
        lines: list[str] = []
        if to_remove:
            elems = ", ".join(f"{ip}/{cidr}" for ip, cidr in to_remove)
            lines.append(f"delete element inet {self.table} {table_set} {{ {elems} }}")
        if to_add:
            elems = ", ".join(f"{ip}/{cidr}" for ip, cidr in to_add)
            lines.append(f"add element inet {self.table} {table_set} {{ {elems} }}")
        self._run_script("\n".join(lines) + "\n")
        self._log.debug(
            "diff applied",
            table_set=table_set,
            added=len(to_add),
            removed=len(to_remove),
        )

    # ── Allowlist ─────────────────────────────────────────────────────────────

    def add_to_allowlist(self, ip: str, cidr: int = 32) -> None:
        self._run_script(f"add element inet {self.table} xbl_allowlist {{ {ip}/{cidr} }}\n")
        self._log.info("allowlist add", ip=ip, cidr=cidr)

    def remove_from_allowlist(self, ip: str, cidr: int = 32) -> None:
        self._run_script(f"delete element inet {self.table} xbl_allowlist {{ {ip}/{cidr} }}\n")
        self._log.info("allowlist remove", ip=ip, cidr=cidr)

    def list_allowlist(self) -> list[tuple[str, int]]:
        output = self._run(["list", "set", "inet", self.table, "xbl_allowlist"])
        return _parse_set_elements(output)

    def replace_allowlist(self, entries: list[tuple[str, int]]) -> None:
        """Replace the entire allowlist via a single ``nft -f`` call.

        Overlapping entries are collapsed via :func:`_collapse_entries` before
        being applied — nftables sets with ``flags interval`` reject overlapping
        elements at the kernel level.

        Applies ``flush set`` followed by ``add element`` in one script file.
        Not a true kernel transaction — a process crash between the two
        operations would leave the set empty — but it is the best available
        without libnftables bindings.
        """
        entries = _collapse_entries(entries)
        lines = [f"flush set inet {self.table} xbl_allowlist"]
        if entries:
            elems = ", ".join(f"{ip}/{cidr}" for ip, cidr in entries)
            lines.append(f"add element inet {self.table} xbl_allowlist {{ {elems} }}")
        self._run_script("\n".join(lines) + "\n")
        self._log.info("allowlist replaced", count=len(entries))


class NoopNftManager:
    """Null-object ``_BlocklistManager`` used when nftables is disabled.

    Route handlers call ``reconcile_blocklist(session, nft_manager)``
    unconditionally.  On Windows dev or when ``XBLP_NFT_ENABLED=false``,
    ``app.state.nft_manager`` holds this class, so the reconciler runs its
    DB-side diff logic but makes no subprocess calls.
    """

    def list_blocklist(self) -> list[tuple[str, int]]:
        return []

    def apply_diff(
        self,
        table_set: str,
        to_add: list[tuple[str, int]],
        to_remove: list[tuple[str, int]],
    ) -> None:
        pass


def _collapse_entries(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge overlapping and adjacent CIDRs into a minimal non-overlapping set.

    Required before passing entries to nftables sets with ``flags interval``:
    the kernel rejects duplicate or overlapping elements at insert time.
    Uses ``strict=False`` so host addresses like ``1.2.3.4/24`` are normalised
    to their network address (``1.2.3.0/24``) before collapsing.
    """
    if not entries:
        return []
    networks = [ipaddress.IPv4Network(f"{ip}/{cidr}", strict=False) for ip, cidr in entries]
    return [
        (str(net.network_address), net.prefixlen) for net in ipaddress.collapse_addresses(networks)
    ]


def _parse_set_elements(output: str) -> list[tuple[str, int]]:
    """Parse IP(/CIDR) entries from the text output of ``nft list set``."""
    match = re.search(r"elements\s*=\s*\{([^}]*)\}", output)
    if not match:
        return []
    results: list[tuple[str, int]] = []
    for token in re.split(r"[,\s]+", match.group(1).strip()):
        token = token.strip()
        if not token:
            continue
        if "/" in token:
            ip, cidr_s = token.split("/", 1)
            results.append((ip.strip(), int(cidr_s.strip())))
        else:
            results.append((token, 32))
    return results
