# deploy/network — Bridge configuration (developer tool)

These scripts convert a running Debian 12 system into the transparent L2 bridge
appliance described in DESIGN.md §3. They are **developer and contributor tools**,
not end-user tools.

## Who these scripts are for

| Audience | What they do instead |
|----------|---------------------|
| End users (v1.0) | Flash a pre-built SD image — the bridge is baked in at build time |
| Developers / contributors | Run `bring-up-bridge.sh` on their own R4S to set up a test environment |
| Future `deploy/install.sh` | Adapts the logic here for Tier 2/3 live installs (Phase 5, DESIGN.md §14.2) |

End users of the released product will never see or run these scripts. The bridge
configuration is applied once during image build (Phase 5) and is already active
when the SD card is inserted.

## Files

| File | Purpose |
|------|---------|
| `br0.conf` | ifupdown snippet installed to `/etc/network/interfaces.d/br0.conf`. Contains `__WAN_IFACE__`/`__LAN_IFACE__` placeholders substituted at install time. |
| `modules-br_netfilter.conf` | Source for `/etc/modules-load.d/xblp-bridge.conf`. Loads `br_netfilter` at boot so nftables can filter bridged traffic. |
| `sysctl-bridge.conf` | Source for `/etc/sysctl.d/99-xblp-bridge.conf`. Sets `bridge-nf-call-iptables=1`. |
| `bring-up-bridge.sh` | Idempotent bring-up script (see below). Run once on a fresh R4S. |
| `confirm-bridge.sh` | Run after reconnecting via mDNS to cancel the safety reboot and disarm rollback. |
| `rollback-bridge.sh` | Restore pre-bridge network config and reboot. Called manually or by the rollback service at boot. |
| `xblp-bridge-rollback.service` | Early-boot oneshot that auto-recovers if a bring-up was never confirmed. |

## Bring-up flow

```
[Developer SSH'd into R4S on eth0's DHCP IP]
        │
        ▼
sudo bash deploy/network/bring-up-bridge.sh
        │
        ├─ Validates ≥2 physical Ethernet interfaces
        ├─ Auto-detects WAN (interface with default route) and LAN
        ├─ Prints detected roles — waits for 'yes' confirmation
        ├─ Backs up /etc/network/ to /etc/network/interfaces.xblp-backup/
        ├─ Arms 10-minute auto-reboot safety timer
        ├─ Installs rollback service + script
        ├─ Writes new br0.conf (substituted from template)
        └─ Detached: kills orphaned dhclient, flushes slave IPs,
           runs `systemctl restart networking`  ← SSH drops here

[Wait ~30 seconds for DHCP on br0 and mDNS to propagate]

ssh <user>@xboxlive-protect.local   ← reconnect via br0's new IP
        │
        ▼
sudo deploy/network/confirm-bridge.sh
        ├─ Cancels auto-reboot timer
        └─ Removes /etc/xboxlive-protect/.bridge-pending (disarms rollback)
```

Pass `--wan IFACE` and `--lan IFACE` explicitly if auto-detection fails (multiple
default routes, non-Ethernet default route, etc.). Pass `--yes` to skip the
confirmation prompt for scripted use.

## Auto-reboot safety net

The bring-up script arms `shutdown -r +10` before touching network config. If the
10-minute window passes without `confirm-bridge.sh` being run:

1. The device reboots
2. `xblp-bridge-rollback.service` runs before `network-pre.target`, sees
   `/etc/xboxlive-protect/.bridge-pending`, and restores the backup
3. The device boots with the previous network configuration
4. Reconnect on the old IP and investigate the failure

`confirm-bridge.sh` removes the sentinel. After that, the rollback service is
permanently disarmed (its `ConditionPathExists` never matches) — it's free
insurance for any future re-run of `bring-up-bridge.sh`.

## Running the integration tests

The bridge tests are marked `integration` and require root. The project's default
pytest run (`pytest` without arguments) uses `addopts = "-m unit"` and skips them.

```bash
# After running bring-up-bridge.sh and confirm-bridge.sh:
sudo .venv/bin/pytest tests/integration/test_bridge.py -v -m integration
```

Traffic tests (`@pytest.mark.needs_device`) additionally require a device connected
to the LAN (Xbox-facing) port. They are skipped automatically if no ARP neighbours
are visible on br0:

```bash
# With a device connected to the LAN port:
sudo .venv/bin/pytest tests/integration/test_bridge.py -v -m "integration and needs_device"
```

## Relation to deploy/install.sh (Phase 5)

`deploy/install.sh` (DESIGN.md §14.2) is deferred to Phase 5. When written, it will
call or replicate the logic in `bring-up-bridge.sh` for Tier 2/3 live installs,
adapted for arbitrary interface names and non-interactive execution. These scripts
define the contract that `install.sh` needs to fulfil.
