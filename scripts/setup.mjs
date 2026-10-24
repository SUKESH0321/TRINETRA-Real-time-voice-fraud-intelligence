// One-time setup
//   npm run setup          core: backend virtualenv + backend/frontend dependencies
//   npm run setup:asr      live speech recognition: faster-whisper + the base.en model, then a real self-test
//   npm run setup:aasist   optional synthetic-voice detector: CPU PyTorch + the AASIST reference model
//   npm run setup:ml       both of the above
import { existsSync } from "node:fs";
import path from "node:path";
import { backend, frontend, has, isWin, run, runOk, venvDir, venvPython } from "./lib.mjs";

const args = process.argv.slice(2);
const wantAsr = args.includes("--asr") || args.includes("--ml");
const wantAasist = args.includes("--aasist") || args.includes("--ml");
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

function pipArgs(pkgs) {
  return useUv ? ["uv", ["pip", "install", "--python", venvPython, ...pkgs]] : [venvPython, ["-m", "pip", "install", ...pkgs]];
}
function pipInstall(pkgs) {
  const [cmd, a] = pipArgs(pkgs);
  run(cmd, a);
}

function pythonVersion() {
  const r = runOk(venvPython, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"], { capture: true });
  return r.ok ? r.stdout.trim() : "?";
}

function setupAsr() {
  console.log("\n== Live speech recognition (faster-whisper, CPU) ==");
  const v = pythonVersion();
  console.log(`Backend Python: ${v}`);
  if (parseFloat(v) >= 3.14) {
    console.warn("Warning: Python 3.14 may not have prebuilt wheels for ctranslate2/onnxruntime yet.\n" +
      "         If the install fails, delete backend/.venv and re-run `npm run setup` with Python 3.12 (uv installs it for you).");
  }
  const [cmd, a] = pipArgs(["faster-whisper"]);
  if (!runOk(cmd, a).ok) {
    console.error("\nCould not install faster-whisper into the backend virtualenv.\n" +
      "Check the error above (usually no network, or an unsupported Python version), then re-run `npm run setup:asr`.");
    process.exit(1);
  }
  console.log("\nDownloading the base.en model (about 150 MB, cached afterwards) and running a real transcription self-test...");
  const test = runOk(venvPython, ["-m", "app.transcription"], { cwd: backend });
  if (!test.ok) {
    console.error("\nThe speech model is not ready (see the message above).\n" +
      "If you are offline, connect and re-run `npm run setup:asr`. The model is cached after the first successful download,\n" +
      "so later starts never need the network. Set TRINETRA_MODEL_DIR to choose where it is stored.");
    process.exit(1);
  }
}

function setupAasist() {
  console.log("\n== Optional synthetic-voice detector (AASIST) ==");
  pipInstall(["torch", "--index-url", "https://download.pytorch.org/whl/cpu"]);
  const dir = path.join(backend, "models", "aasist");
  if (!existsSync(dir)) run("git", ["clone", "--depth", "1", "https://github.com/clovaai/aasist.git", dir]);
  else console.log("AASIST reference implementation already present.");
}

createVenv();
pipInstall(["-r", path.join(backend, "requirements.txt")]);
run("npm", ["--prefix", frontend, "install"]);
if (wantAsr) setupAsr();
if (wantAasist) setupAasist();

console.log("\nSetup complete. Start TRINETRA with:  npm run dev");
if (!wantAsr) console.log("Live speech recognition (needed for microphone transcripts):  npm run setup:asr");
if (!wantAasist) console.log("Optional synthetic-voice detector:  npm run setup:aasist");
console.log("If the backend is already running, press Retry in the app (or restart it) to pick up a newly installed model.");
