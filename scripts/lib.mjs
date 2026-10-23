// Shared helpers for the TRINETRA launch scripts. No dependencies: only Node's standard library.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Optional root .env file (KEY=VALUE per line). Real environment variables win.
const envFile = path.join(root, ".env");
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m && !line.trim().startsWith("#") && process.env[m[1]] === undefined) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
}
export const backend = path.join(root, "backend");
export const frontend = path.join(root, "frontend");
export const isWin = process.platform === "win32";
export const venvDir = path.join(backend, ".venv");
export const venvPython = path.join(venvDir, isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");

export function has(cmd, args = ["--version"]) {
  const r = spawnSync(cmd, args, { stdio: "ignore", shell: isWin });
  return r.status === 0;
}

export function run(cmd, args, opts = {}) {
  console.log(`\n> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { stdio: "inherit", shell: isWin && !cmd.endsWith(".exe"), cwd: root, ...opts });
  if (r.status !== 0) {
    console.error(`\nCommand failed (exit ${r.status}): ${cmd} ${args.join(" ")}`);
    process.exit(r.status ?? 1);
  }
}

export function ensureVenv() {
  if (!existsSync(venvPython)) {
    console.error("Backend virtualenv not found. Run:  npm run setup");
    process.exit(1);
  }
}

export function start(name, cmd, args, opts = {}) {
  const child = spawn(cmd, args, { stdio: ["inherit", "pipe", "pipe"], shell: isWin && !cmd.endsWith(".exe"), cwd: root, ...opts });
  const tag = `[${name}]`;
  const pipe = (stream, out) => stream.on("data", (d) => String(d).split(/\r?\n/).filter(Boolean).forEach((l) => out.write(`${tag} ${l}\n`)));
  pipe(child.stdout, process.stdout);
  pipe(child.stderr, process.stderr);
  return child;
}
