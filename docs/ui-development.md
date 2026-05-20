# UI Development Guide

The web UI is a Vite + React 18 + TypeScript SPA located in `ui/`. It is
served in production by the FastAPI daemon as static files. In development
it runs on a Vite dev server that proxies API calls to the real R4S appliance
on your LAN.

## Prerequisites

- Node.js 18+ (Debian 12 ships 18.19; on macOS/Windows install via
  [nodejs.org](https://nodejs.org) or nvm)
- A NanoPi R4S (or any device running `xblp-api`) on the same LAN
- `xboxlive-protect.local` resolves from your workstation via mDNS
  (natively supported on Windows 10/11+, macOS, and Linux with `avahi-daemon`)

## Running the dev server

```bash
npm --prefix ui install       # first time only
npm --prefix ui run dev       # starts on http://localhost:5173
```

Open `http://localhost:5173` in your browser. The Vite dev server proxies
`/api/*` requests to `https://xboxlive-protect.local` with TLS verification
disabled (`secure: false`), so you do **not** need to accept a cert warning
in your browser — the proxy handles TLS.

### Cookie handling across the proxy

The R4S sets session cookies with the `Secure` attribute (required for HTTPS).
The Vite proxy strips this flag from `Set-Cookie` headers before they reach the
browser, so cookies are stored and sent back correctly over plain HTTP at
`localhost:5173`. `SameSite=Strict` is preserved and works correctly because all
API calls are same-origin from the browser's perspective.

### If mDNS doesn't resolve `xboxlive-protect.local`

Create `ui/.env.local` (git-ignored) and set the target directly:

```
VITE_API_TARGET=https://192.168.1.x
```

Replace `192.168.1.x` with the R4S's actual LAN IP. This is common when
developing under WSL, corporate VPNs, or other setups that block mDNS.

## Build output

```bash
npm --prefix ui run build     # produces ui/dist/
```

The FastAPI daemon mounts `ui/dist/` at `/` via `StaticFiles`. The dist
directory is set by the `XBLP_UI_DIST_PATH` environment variable (default
`/opt/xboxlive-protect/ui/dist`, which resolves correctly for both the dev
symlink layout and the Phase 5 real install).

The dist directory is gitignored. On the R4S the installer builds it during
`deploy/install-stage1.sh`.

## Running JavaScript tests

```bash
npm --prefix ui test            # run once
npm --prefix ui run test:watch  # watch mode
```

Tests use Vitest 2 + `@testing-library/react` + jsdom. They run on Windows
and Linux, no R4S required.

## Project structure

```
ui/
├── index.html                    mobile viewport meta, root <div id="root">
├── vite.config.ts                dev-server proxy, Vitest config
├── tailwind.config.js            darkMode: 'media', content paths
├── src/
│   ├── api/
│   │   ├── client.ts             fetch wrapper, ApiError
│   │   ├── auth.ts               login, logout, changePassword, getMe
│   │   └── status.ts             getStatus
│   ├── hooks/
│   │   └── useAuth.ts            React Query over /api/v1/auth/me
│   ├── components/
│   │   ├── RequireAuth.tsx       Outlet-based route guard
│   │   ├── Layout.tsx            top bar + footer shell
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   └── FormError.tsx
│   └── screens/
│       ├── Login.tsx
│       ├── ChangePassword.tsx
│       └── Dashboard.tsx         fetches /api/v1/status, placeholder
```

## SPA routing

Client-side routing is handled by `react-router-dom` v6 with `BrowserRouter`.
The FastAPI daemon registers a `_SPAStaticFiles` mount that serves `index.html`
for any path not matching a real static file, so browser refreshes on `/dashboard`
and similar paths work correctly.

Any path under `/api/v1/` that doesn't match a real endpoint returns a JSON 404
(not `index.html`), so curl scripts and automation get a debuggable error.
