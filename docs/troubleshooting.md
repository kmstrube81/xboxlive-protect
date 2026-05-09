# xboxlive-protect — Troubleshooting Guide

## Bridge recovery procedures

Stage 5 converts the device from a regular host into a transparent L2 bridge.
This is the first stage that can render the device unreachable over the network.
The bring-up script includes a layered safety net; this document explains how
each layer works and what to do if something goes wrong.

---

### How the safety net works

When you run `bring-up-bridge.sh`:

1. **Backup** — The script copies `/etc/network/interfaces` and all of
   `/etc/network/interfaces.d/` to `/etc/network/interfaces.xblp-backup/`.

2. **Sentinel** — The script writes `/etc/xboxlive-protect/.bridge-pending`.
   This file's presence signals "a bring-up was attempted but not confirmed."

3. **Rollback service** — `xblp-bridge-rollback.service` is installed and
   enabled. It runs at early boot (before `network-pre.target`) and checks
   for the sentinel. If the sentinel is present, it restores the backup and
   removes the sentinel before the network stack starts.

4. **Reboot timer** — `shutdown -r +10` is armed. If you don't confirm the
   bridge within 10 minutes, the device reboots — triggering the rollback
   service — and comes back on the old network config.

5. **Confirm** — After SSHing back in via `xboxlive-protect.local`, you run
   `confirm-bridge.sh`, which cancels the reboot timer and removes the
   sentinel. The rollback service remains installed but permanently disarmed
   (it only acts when the sentinel exists).

---

### Scenario 1: Bring-up succeeded, can reconnect

```
# On the R4S (after SSH back in via xboxlive-protect.local):
sudo deploy/network/confirm-bridge.sh
```

This is the happy path. The reboot timer is cancelled and the sentinel is
removed. Future reboots use the bridge configuration normally.

---

### Scenario 2: Cannot reconnect — waiting for the auto-reboot

If `xboxlive-protect.local` is not reachable and you cannot determine the
new br0 IP from your router's DHCP lease table, do nothing. The device will
auto-reboot within 10 minutes, the rollback service restores the old config,
and the device comes back on its original IP with mDNS advertising
`xboxlive-protect.local` as before.

**After the auto-reboot**, SSH in normally and investigate the failure before
retrying `bring-up-bridge.sh`:

```bash
# Check what happened (journald may have captured networking errors)
sudo journalctl -b -1 -u networking
sudo journalctl -b -1 -u xblp-bridge-rollback

# Re-run with explicit interface names to bypass auto-detection
sudo bash deploy/network/bring-up-bridge.sh --wan eth0 --lan eth1
```

---

### Scenario 3: Manual rollback (still SSH'd in, want to abort)

If you are still connected and want to undo before the 10-minute timer fires:

```bash
sudo /usr/local/lib/xboxlive-protect/rollback-bridge.sh
```

This restores the backup files and reboots immediately. The rollback script
cancels the pending shutdown timer, then issues `shutdown -r now`. You will
lose this SSH session; reconnect after the device comes back up.

---

### Scenario 4: Device rebooted before rollback service was installed

If `bring-up-bridge.sh` failed before it could install
`xblp-bridge-rollback.service` (e.g., apt failure after the reboot timer was
armed), the rollback service is not present. On reboot, networking tries the
new config. If it fails, the device is unreachable.

**Recovery**: Physical console access or SD card recovery.

- If you have a USB-to-serial adapter and console access, log in and run:
  ```bash
  cp -a /etc/network/interfaces.xblp-backup/. /etc/network/
  rm -f /etc/xboxlive-protect/.bridge-pending
  reboot
  ```

- If you have access to the SD card from another machine, mount the root
  partition and perform the same file restoration manually.

This scenario should not occur in normal use because `bring-up-bridge.sh`
installs the rollback service before arming the reboot timer.

---

### Scenario 5: br0 exists with wrong members from a failed run

If a previous `bring-up-bridge.sh` partially configured the bridge (created
br0 but with the wrong ports), the script will refuse to proceed:

```
[ERROR] br0 already exists with unexpected member count ...
```

**Recovery options:**

A. Reboot — if the sentinel is still present, the rollback service restores
   the old config automatically.

B. Manual teardown — if you can SSH in and br0 is the management interface:
   ```bash
   sudo /usr/local/lib/xboxlive-protect/rollback-bridge.sh
   ```

C. If neither option works and the sentinel is not present (bridge was
   confirmed but is wrong), you can manually remove br0 and restore:
   ```bash
   sudo ip link set eth0 nomaster
   sudo ip link set eth1 nomaster
   sudo ip link del br0
   sudo cp -a /etc/network/interfaces.xblp-backup/. /etc/network/
   sudo systemctl restart networking
   ```

---

## nftables and the bridge — how they interact

### Why br_netfilter is required

The nftables ruleset in `table inet xblp` hooks the netfilter `forward` chain:

```
chain forward {
    type filter hook forward priority 0;
    ip saddr @xbl_allowlist accept
    ip daddr @xbl_allowlist accept
    ip saddr @blocklist drop
    ip daddr @blocklist drop
}
```

The `inet` family forward hook is part of the IP-layer netfilter framework.
By default, Ethernet frames forwarded by the Linux bridge bypass this hook
entirely — the bridge operates at Layer 2 and the IP-layer hooks are not
called for bridged traffic.

Loading `br_netfilter` and setting `net.bridge.bridge-nf-call-iptables=1`
instructs the kernel to route bridged IPv4 packets through the IP-layer
netfilter hooks. After this, Xbox ↔ internet traffic passes through the
`inet xblp forward` chain and the blocklist takes effect.

Without `br_netfilter`, the nftables ruleset is silently ineffective for
bridged traffic. The bridge still forwards traffic; it is just unfiltered.

### Management plane is not affected

Traffic to and from the bridge's own IP address (br0's DHCP address, used for
SSH and the management UI) passes through the `input` and `output` hooks, not
`forward`. The `forward` hook only sees traffic crossing the bridge from one
physical port to the other — i.e., traffic between the Xbox and the internet.

This means:
- SSH sessions to `xboxlive-protect.local` are unaffected by the blocklist.
- Even if an IP were mistakenly added to the blocklist, it would not lock you
  out of the device. (Blocklist entries only affect traffic the Xbox exchanges
  with that IP, not traffic originating from the device itself.)
- DHCP renewals on br0 are unaffected.

### Verifying nftables fires on bridged traffic

After bridge bring-up and confirm, you can verify the forward chain is seeing
traffic by adding a temporary counter rule during an active session:

```bash
# Add a counting rule
sudo nft add rule inet xblp forward counter comment "test"

# Check the counter (should increment as Xbox traffic flows)
sudo nft list chain inet xblp forward

# Remove the test rule (find the handle number from the list output)
sudo nft delete rule inet xblp forward handle <N>
```

If the counter does not increment after 10–15 seconds of Xbox activity,
check:

```bash
lsmod | grep br_netfilter             # module must be loaded
sysctl net.bridge.bridge-nf-call-iptables   # must be 1
bridge link show master br0           # eth0 and eth1 must be members
```

---

## Common issues

### mDNS not resolving after bridge bring-up

Avahi-daemon may take 10–20 seconds to advertise the new br0 IP via mDNS.
Wait, then try:

```bash
avahi-browse -a -t -r 2>/dev/null | grep xboxlive-protect
```

If avahi is not running after the networking restart:

```bash
sudo systemctl status avahi-daemon
sudo systemctl restart avahi-daemon
```

### DHCP not assigned to br0

If `ip addr show br0` shows no IPv4 address after ~30 seconds:

```bash
# Check dhclient status
sudo journalctl -u networking --since "5 minutes ago"

# Manually request a lease
sudo dhclient br0
```

If the router is not assigning a DHCP lease to br0, it may be filtering by
MAC address. br0 inherits its MAC from the first bridge member added (usually
the WAN port). The MAC visible to the router changes from the old NIC's MAC
to br0's inherited MAC. Check your router's DHCP client list for an unknown
MAC.

### Bridge not forwarding traffic (Xbox loses internet)

```bash
# Verify both ports are in FORWARDING state (not BLOCKING or DISABLED)
bridge link show

# Verify STP is disabled (STP causes 30 s blocking on port-up)
cat /sys/class/net/br0/bridge/stp_state   # should be 0

# Check for any firewall INPUT rules that might be interfering
sudo nft list table inet xblp
```

If STP is blocking ports, the forward delay can be forced:

```bash
sudo ip link set br0 type bridge forward_delay 0
```

---

## Uninstalling the bridge

To revert to a non-bridge configuration permanently:

```bash
# Stop and disable the rollback service
sudo systemctl disable xblp-bridge-rollback.service
sudo rm /etc/systemd/system/xblp-bridge-rollback.service
sudo systemctl daemon-reload

# Restore the original network config
sudo cp -a /etc/network/interfaces.xblp-backup/. /etc/network/
sudo rm -f /etc/network/interfaces.d/br0.conf

# Remove the kernel config files
sudo rm -f /etc/modules-load.d/xblp-bridge.conf
sudo rm -f /etc/sysctl.d/99-xblp-bridge.conf

# Remove the sentinel if present
sudo rm -f /etc/xboxlive-protect/.bridge-pending

# Reboot to apply
sudo reboot
```
