"""Unit tests for xblp_common.nft — all subprocess calls are mocked."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xblp_common.nft import NftError, NftManager, _collapse_entries, _parse_set_elements

pytestmark = pytest.mark.unit

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "deploy" / "nftables" / "xblp.nft.template"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _rendered(table: str = "xblp") -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8").replace("{table}", table)


# ── Test double ───────────────────────────────────────────────────────────────


class _RunStub:
    """Configurable fake for ``subprocess.run`` in nft unit tests.

    Reads script file content when called with ``-f <path>`` so tests can
    assert on what nft would have been asked to execute without touching the
    real binary.

    *responses* is a list of ``(returncode, stdout, stderr)`` tuples consumed
    in order. Once exhausted, subsequent calls return ``(0, "", "")``.
    """

    def __init__(self, responses: list[tuple[int, str, str]] | None = None) -> None:
        self._responses = responses or []
        self._idx = 0
        self.scripts: list[str] = []
        self.cmds: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kw: object) -> MagicMock:
        self.cmds.append(cmd)
        if "-f" in cmd:
            idx = cmd.index("-f")
            self.scripts.append(Path(cmd[idx + 1]).read_text(encoding="utf-8"))
        if self._idx < len(self._responses):
            rc, stdout, stderr = self._responses[self._idx]
        else:
            rc, stdout, stderr = 0, "", ""
        self._idx += 1
        return MagicMock(returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture
def mgr() -> NftManager:
    return NftManager(nft_bin="/usr/sbin/nft")


# ── Error handling ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_nft_error_raised_on_nonzero_exit(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(1, "", "table not found: xblp")])
    with (
        patch("xblp_common.nft.subprocess.run", stub),
        pytest.raises(NftError, match="table not found: xblp"),
    ):
        mgr.list_blocklist()


@pytest.mark.unit
def test_nft_error_message_includes_stderr(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(2, "", "some specific error detail")])
    with patch("xblp_common.nft.subprocess.run", stub), pytest.raises(NftError) as exc_info:
        mgr.remove_ruleset()
    assert "some specific error detail" in str(exc_info.value)


# ── Binary path configuration ─────────────────────────────────────────────────


@pytest.mark.unit
def test_env_var_sets_nft_bin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XBLP_NFT_BIN", "/custom/nft")
    assert NftManager().nft_bin == "/custom/nft"


@pytest.mark.unit
def test_constructor_arg_takes_precedence_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XBLP_NFT_BIN", "/custom/nft")
    assert NftManager(nft_bin="/explicit/nft").nft_bin == "/explicit/nft"


@pytest.mark.unit
def test_default_bin_when_no_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XBLP_NFT_BIN", raising=False)
    assert NftManager().nft_bin == "/usr/sbin/nft"


# ── verify_ruleset_present ────────────────────────────────────────────────────


@pytest.mark.unit
def test_verify_ruleset_present_returns_true_when_chain_exists(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        assert mgr.verify_ruleset_present() is True
    assert stub.cmds[0] == ["/usr/sbin/nft", "list", "chain", "inet", "xblp", "forward"]


@pytest.mark.unit
def test_verify_ruleset_present_returns_false_on_nft_error(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(1, "", "no such chain")])
    with patch("xblp_common.nft.subprocess.run", stub):
        assert mgr.verify_ruleset_present() is False


# ── apply_initial_ruleset ────────────────────────────────────────────────────


@pytest.mark.unit
def test_apply_initial_ruleset_when_absent_applies_template(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(1, "", "no such table")])  # verify → absent; then script succeeds
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.apply_initial_ruleset()
    assert len(stub.scripts) == 1
    assert "table inet xblp" in stub.scripts[0]
    assert "chain forward" in stub.scripts[0]


@pytest.mark.unit
def test_apply_initial_ruleset_when_present_is_noop(mgr: NftManager) -> None:
    stub = _RunStub()  # verify succeeds → present; no further calls
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.apply_initial_ruleset()
    assert stub.scripts == []
    assert stub._idx == 1  # only the verify call


@pytest.mark.unit
def test_apply_initial_ruleset_idempotent(mgr: NftManager) -> None:
    """Second call skips the template application when the ruleset is present."""
    stub = _RunStub(
        responses=[
            (1, "", "no such table"),  # first call: verify → absent → apply
            (0, "", ""),  # first call: nft -f script
            (0, "", ""),  # second call: verify → present → skip
        ]
    )
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.apply_initial_ruleset()
        mgr.apply_initial_ruleset()
    assert len(stub.scripts) == 1  # template applied exactly once


# ── Blocklist operations ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_add_to_blocklist_script_content(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.add_to_blocklist("1.2.3.4", 32)
    assert len(stub.scripts) == 1
    assert "add element inet xblp blocklist" in stub.scripts[0]
    assert "1.2.3.4/32" in stub.scripts[0]


@pytest.mark.unit
def test_add_to_blocklist_default_cidr_is_32(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.add_to_blocklist("5.6.7.8")
    assert "5.6.7.8/32" in stub.scripts[0]


@pytest.mark.unit
def test_remove_from_blocklist_script_content(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.remove_from_blocklist("1.2.3.0", 24)
    assert "delete element inet xblp blocklist" in stub.scripts[0]
    assert "1.2.3.0/24" in stub.scripts[0]


# ── Allowlist operations ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_add_to_allowlist_script_content(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.add_to_allowlist("10.0.0.1", 32)
    assert "add element inet xblp xbl_allowlist" in stub.scripts[0]
    assert "10.0.0.1/32" in stub.scripts[0]


@pytest.mark.unit
def test_remove_from_allowlist_script_content(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.remove_from_allowlist("10.0.0.0", 8)
    assert "delete element inet xblp xbl_allowlist" in stub.scripts[0]
    assert "10.0.0.0/8" in stub.scripts[0]


# ── remove_ruleset ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_remove_ruleset_calls_delete_table(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.remove_ruleset()
    assert stub.cmds[0] == ["/usr/sbin/nft", "delete", "table", "inet", "xblp"]


# ── list_blocklist parsing ────────────────────────────────────────────────────

_NFT_SET_WITH_ELEMENTS = """\
table inet xblp {
\tset blocklist {
\t\ttype ipv4_addr
\t\tflags interval
\t\telements = { 1.2.3.4, 203.0.113.0/24,
\t\t\t     198.51.100.1 }
\t}
}
"""

_NFT_SET_EMPTY = """\
table inet xblp {
\tset blocklist {
\t\ttype ipv4_addr
\t\tflags interval
\t}
}
"""


@pytest.mark.unit
def test_list_blocklist_parses_host_addresses(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(0, _NFT_SET_WITH_ELEMENTS, "")])
    with patch("xblp_common.nft.subprocess.run", stub):
        result = mgr.list_blocklist()
    assert ("1.2.3.4", 32) in result
    assert ("198.51.100.1", 32) in result


@pytest.mark.unit
def test_list_blocklist_parses_cidr_entries(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(0, _NFT_SET_WITH_ELEMENTS, "")])
    with patch("xblp_common.nft.subprocess.run", stub):
        result = mgr.list_blocklist()
    assert ("203.0.113.0", 24) in result


@pytest.mark.unit
def test_list_blocklist_empty_set_returns_empty_list(mgr: NftManager) -> None:
    stub = _RunStub(responses=[(0, _NFT_SET_EMPTY, "")])
    with patch("xblp_common.nft.subprocess.run", stub):
        result = mgr.list_blocklist()
    assert result == []


# ── replace_allowlist ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_replace_allowlist_with_entries(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.replace_allowlist([("1.2.3.4", 32), ("10.0.0.0", 8)])
    script = stub.scripts[0]
    assert "flush set inet xblp xbl_allowlist" in script
    assert "add element inet xblp xbl_allowlist" in script
    assert "1.2.3.4/32" in script
    assert "10.0.0.0/8" in script


@pytest.mark.unit
def test_replace_allowlist_empty_only_flushes(mgr: NftManager) -> None:
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.replace_allowlist([])
    script = stub.scripts[0]
    assert "flush set inet xblp xbl_allowlist" in script
    assert "add element" not in script


# ── Table name propagation ────────────────────────────────────────────────────


@pytest.mark.unit
def test_custom_table_name_used_in_list_command() -> None:
    mgr = NftManager(nft_bin="/usr/sbin/nft", table="xblp_test")
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.list_blocklist()
    assert "xblp_test" in stub.cmds[0]
    assert "xblp" not in [a for a in stub.cmds[0] if a != "xblp_test"]


@pytest.mark.unit
def test_custom_table_name_used_in_scripts() -> None:
    mgr = NftManager(nft_bin="/usr/sbin/nft", table="xblp_test")
    stub = _RunStub()
    with patch("xblp_common.nft.subprocess.run", stub):
        mgr.add_to_blocklist("1.2.3.4")
    assert "xblp_test" in stub.scripts[0]


# ── _parse_set_elements unit tests ────────────────────────────────────────────


@pytest.mark.unit
def test_parse_set_elements_host_address() -> None:
    assert _parse_set_elements(_NFT_SET_WITH_ELEMENTS) == [
        ("1.2.3.4", 32),
        ("203.0.113.0", 24),
        ("198.51.100.1", 32),
    ]


@pytest.mark.unit
def test_parse_set_elements_no_elements_block() -> None:
    assert _parse_set_elements(_NFT_SET_EMPTY) == []


# ── Snapshot test ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_initial_ruleset_snapshot() -> None:
    """Rendered template must match the stored snapshot exactly.

    If you intentionally change the template, update
    tests/unit/fixtures/nft_initial_ruleset.nft to match.
    """
    rendered = _rendered()
    expected = (_FIXTURES_DIR / "nft_initial_ruleset.nft").read_text(encoding="utf-8")
    assert rendered == expected, (
        "Ruleset template changed unexpectedly. "
        "If intentional, update tests/unit/fixtures/nft_initial_ruleset.nft."
    )


# ── Structural invariant tests ────────────────────────────────────────────────


@pytest.mark.unit
def test_table_is_inet_family() -> None:
    """Table family must be 'inet', not 'ip'.

    'inet' covers both IPv4 and IPv6 hooks; 'ip' would only hook IPv4 forward
    traffic, silently missing IPv6 peers.
    """
    assert re.search(r"table\s+inet\s+xblp\b", _rendered()), (
        "Table must be declared as 'table inet xblp', not 'table ip xblp' or other family."
    )


@pytest.mark.unit
def test_blocklist_set_has_interval_flag() -> None:
    """blocklist must declare 'flags interval' to support CIDR prefixes.

    Without it, nft rejects prefix entries like 203.0.113.0/24 at add time.
    """
    match = re.search(r"set blocklist \{([^}]*)\}", _rendered(), re.DOTALL)
    assert match, "blocklist set not found in rendered template"
    assert "flags interval" in match.group(1), (
        "blocklist set must include 'flags interval' to support CIDR prefixes."
    )


@pytest.mark.unit
def test_allowlist_set_has_interval_flag() -> None:
    """xbl_allowlist must declare 'flags interval' to support CIDR ranges.

    The Xbox Live allowlist contains /16 and /24 prefixes from the Azure
    service tag JSON — host-only sets would silently drop them.
    """
    match = re.search(r"set xbl_allowlist \{([^}]*)\}", _rendered(), re.DOTALL)
    assert match, "xbl_allowlist set not found in rendered template"
    assert "flags interval" in match.group(1), (
        "xbl_allowlist set must include 'flags interval' to support CIDR prefixes."
    )


@pytest.mark.unit
def test_forward_chain_hooks_at_priority_zero() -> None:
    """The forward chain must hook at priority 0 (standard filter position).

    A non-zero priority could place our rules before or after other kernel
    hooks in unexpected ways.
    """
    assert re.search(r"type filter hook forward priority 0\b", _rendered()), (
        "forward chain must declare 'type filter hook forward priority 0'."
    )


@pytest.mark.unit
def test_chain_checks_allowlist_before_blocklist() -> None:
    """Allowlist accept rules must appear before blocklist drop rules.

    This is the core safety property: a subscription or user error can block
    arbitrary IPs, but it must never be able to block Microsoft Xbox Live
    infrastructure. The allowlist accept is the guarantee.
    """
    rendered = _rendered()
    allowlist_pos = rendered.find("@xbl_allowlist accept")
    blocklist_pos = rendered.find("@blocklist drop")
    assert allowlist_pos != -1 and blocklist_pos != -1, (
        "Expected both @xbl_allowlist accept and @blocklist drop rules in the template."
    )
    assert allowlist_pos < blocklist_pos, (
        "Allowlist accept rules must precede blocklist drop rules. "
        "If reversed, a subscription could block Microsoft IPs."
    )


# ── _collapse_entries unit tests ──────────────────────────────────────────────


@pytest.mark.unit
def test_collapse_entries_empty() -> None:
    assert _collapse_entries([]) == []


@pytest.mark.unit
def test_collapse_entries_single_host_passthrough() -> None:
    assert _collapse_entries([("1.2.3.4", 32)]) == [("1.2.3.4", 32)]


@pytest.mark.unit
def test_collapse_entries_non_overlapping_passthrough() -> None:
    result = _collapse_entries([("1.2.3.4", 32), ("5.6.7.8", 32)])
    assert set(result) == {("1.2.3.4", 32), ("5.6.7.8", 32)}


@pytest.mark.unit
def test_collapse_entries_host_inside_cidr_absorbed() -> None:
    """A /32 contained in a /24 collapses to just the /24."""
    result = _collapse_entries([("203.0.113.4", 32), ("203.0.113.0", 24)])
    assert result == [("203.0.113.0", 24)]


@pytest.mark.unit
def test_collapse_entries_adjacent_slash25s_merge_to_slash24() -> None:
    """Two adjacent /25s that together fill a /24 are merged into that /24."""
    result = _collapse_entries([("10.0.0.0", 25), ("10.0.0.128", 25)])
    assert result == [("10.0.0.0", 24)]


@pytest.mark.unit
def test_collapse_entries_multiple_overlapping() -> None:
    """A /32 and a /25 that are both subsets of a /24 all collapse to the /24."""
    entries = [("192.0.2.0", 24), ("192.0.2.1", 32), ("192.0.2.128", 25)]
    result = _collapse_entries(entries)
    assert result == [("192.0.2.0", 24)]
