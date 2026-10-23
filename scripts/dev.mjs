// npm run dev: backend (port 8000) + Vite dev server (port 5173) with one command.
import { ensureVenv, frontend, start, venvPython, backend } from "./lib.mjs";

ensureVenv();
const host = process.env.TRINETRA_HOST ?? "127.0.0.1";
const api = start("api", venvPython, ["-m", "uvicorn", "app.main:app", "--host", host, "--port", "8000"], { cwd: backend });
const https = process.argv.includes("--https");
const web = start("web", "npm", ["--prefix", frontend, "run", "dev"], { env: { ...process.env, ...(https ? { TRINETRA_HTTPS: "1" } : {}) } });

console.log(`\nTRINETRA dev servers starting. Open ${https ? "https" : "http"}://localhost:5173  (API on :8000)`);
if (https) console.log("Self-signed certificate: accept the browser warning. Phones on the same Wi-Fi can open https://<this-computer-ip>:5173\n");

let closing = false;
function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  for (const c of [api, web]) {
    try {
      c.kill();
    } catch {
      /* already stopped */
    }
  }
  setTimeout(() => process.exit(code), 300);
}
api.on("exit", (c) => shutdown(c ?? 1));
web.on("exit", (c) => shutdown(c ?? 1));
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
