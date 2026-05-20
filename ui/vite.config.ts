import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  server: {
    proxy: {
      "/api": {
        // Override via VITE_API_TARGET=https://192.168.x.x in .env.local
        // when mDNS doesn't resolve on your workstation.
        target:
          process.env["VITE_API_TARGET"] ?? "https://xboxlive-protect.local",
        changeOrigin: true,
        secure: false, // accept the R4S self-signed cert in the proxy layer
        configure(proxy) {
          // Strip the Secure cookie flag so the browser stores the session
          // cookie over plain http://localhost:5173.  SameSite=Strict stays
          // as-is: all API calls are same-origin from the browser's view.
          proxy.on("proxyRes", (proxyRes) => {
            const cookies = proxyRes.headers["set-cookie"];
            if (cookies) {
              proxyRes.headers["set-cookie"] = cookies.map((c) =>
                c.replace(/;\s*Secure/gi, ""),
              );
            }
          });
        },
      },
    },
  },

  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
