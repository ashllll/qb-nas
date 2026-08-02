const { existsSync } = require("node:fs");
const { spawnSync } = require("node:child_process");
const { join } = require("node:path");

const executable =
  process.platform === "win32"
    ? join(".venv", "Scripts", "python.exe")
    : join(".venv", "bin", "python");

if (!existsSync(executable)) {
  console.error(`Python virtual environment not found: ${executable}`);
  process.exit(1);
}

const result = spawnSync(executable, process.argv.slice(2), {
  stdio: "inherit",
  shell: false,
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
if (result.signal) {
  if (process.platform !== "win32") {
    process.kill(process.pid, result.signal);
  }
  console.error(`Python process terminated by ${result.signal}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
