# xboxlive-protect

![Status: Pre-alpha](https://img.shields.io/badge/status-pre--alpha-red)
![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)

A transparent network bridge appliance that sits between your Xbox console and your router, watches peer-to-peer game traffic, identifies likely lobby hosts, and lets you manually block their IPs — all from a local web UI accessible on your phone. Built for retro Xbox Live communities where modded lobbies in older P2P-hosted games (notably Modern Warfare 2 on Xbox 360) are a persistent problem.

## What is this?

xboxlive-protect runs on a small single-board computer (reference hardware: FriendlyElec NanoPi R4S, ~$80 all-in) placed inline between your Xbox and your router. It operates as a transparent Layer 2 bridge — your Xbox sees the network exactly as before, with no added latency under normal operation. When you join a lobby and suspect a modder is hosting, you open `https://xboxlive-protect.local` on your phone, see the live peer table ranked by traffic score, and tap Block on the likely host. The block is applied immediately at the bridge level via nftables.

Key properties:

- **All blocks are manual** — no automatic blocking, ever
- **Xbox Live infrastructure is protected** — Microsoft service IPs can never be blocked, regardless of what any blocklist says
- **Profile-driven detection** — per-game YAML profiles define what traffic patterns to look for
- **Community blocklists** — subscribe to remote lists, import/export your own
- **Flash and forget** — ships as a flashable SD card image; no Linux knowledge required

## Status

**Pre-implementation.** The design specification is complete; code does not yet exist.

See [DESIGN.md](DESIGN.md) for the full design specification and [Section 15](DESIGN.md#15-phased-implementation-plan) for the phased implementation plan.

## Repository layout

See [Section 13 of DESIGN.md](DESIGN.md#13-repository-structure) for the full annotated directory structure.

## License

Code: [GPL-3.0](LICENSE)
Game profiles: CC0-1.0
