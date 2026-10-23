/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = process.env.TRINETRA_BACKEND ?? "http://127.0.0.1:8000";
// `npm run dev:https` serves a self-signed certificate so phones on the LAN may use the microphone.
const useHttps = process.env.TRINETRA_HTTPS === "1";
const extraPlugins = useHttps ? [(await import("@vitejs/plugin-basic-ssl")).default()] : [];

export default defineConfig({
  plugins: [react(), tailwindcss(), ...extraPlugins],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
