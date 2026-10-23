// Starts the FastAPI backend (or runs its tests with --pytest).
import { backend, ensureVenv, run, venvPython } from "./lib.mjs";

ensureVenv();
if (process.argv.includes("--pytest")) {
  run(venvPython, ["-m", "pytest", "-q"], { cwd: backend });
} else if (process.argv.includes("--check-scenarios")) {
  run(venvPython, ["tools/check_scenarios.py"], { cwd: backend });
} else {
  const host = process.env.TRINETRA_HOST ?? "127.0.0.1";
  const port = process.env.TRINETRA_PORT ?? "8000";
  const args = ["-m", "uvicorn", "app.main:app", "--host", host, "--port", port];
  if (process.argv.includes("--reload")) args.push("--reload");
  run(venvPython, args, { cwd: backend });
}
