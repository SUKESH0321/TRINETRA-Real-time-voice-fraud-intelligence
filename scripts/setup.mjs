// One-time setup:  npm run setup        (core: backend venv + frontend deps)
//                  npm run setup:ml     (optional local ML: speech recognition + AASIST)
import { existsSync } from "node:fs";
import path from "node:path";
import { backend, frontend, has, isWin, run, venvDir, venvPython } from "./lib.mjs";

const wantMl = process.argv.includes("--ml");
const useUv = has("uv");

function createVenv() {
  if (existsSync(venvPython)) return console.log("Backend virtualenv already exists, reusing it.");
  if (useUv) return run("uv", ["venv", "--python", "3.12", venvDir]);
  const candidates = isWin ? [["py", ["-3.12"]], ["py", ["-3.11"]], ["python", []]] : [["python3.12", []], ["python3.11", []], ["python3", []]];
  for (const [cmd, pre] of candidates) {
    if (has(cmd, [...pre, "--version"])) return run(cmd, [...pre, "-m", "venv", venvDir]);
  }
  console.error("No suitable Python found. Install Python 3.11 or 3.12 (or install `uv`), then re-run.");
  process.exit(1);
}

function pipInstall(args) {
  if (useUv) run("uv", ["pip", "install", "--python", venvPython, ...args]);
  else run(venvPython, ["-m", "pip", "install", ...args]);
}

createVenv();
pipInstall(["-r", path.join(backend, "requirements.txt")]);
run("npm", ["--prefix", frontend, "install"]);

if (wantMl) {
  console.log("\nInstalling optional ML components (about 250 MB of downloads)...");
  pipInstall(["faster-whisper"]);
  pipInstall(["torch", "--index-url", "https://download.pytorch.org/whl/cpu"]);
  const aasist = path.join(backend, "models", "aasist");
  if (!existsSync(aasist)) run("git", ["clone", "--depth", "1", "https://github.com/clovaai/aasist.git", aasist]);
  console.log("\nDownloading the base.en speech-recognition model (about 150 MB, cached after first run)...");
  run(venvPython, ["-c", "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8'); print('model ready')"]);
}

console.log(`\nSetup complete. Start TRINETRA with:  npm run dev\n${wantMl ? "" : "Optional local speech recognition + AASIST detector:  npm run setup:ml\n"}`);
