import { spawn } from "child_process";

// Set environment variables for Python
const env = { ...process.env, PYTHONUNBUFFERED: "1" };

console.log("Starting Python server...");

// Spawn the python process
const python = spawn("python", ["main.py"], { 
  stdio: "inherit",
  cwd: process.cwd(),
  env
});

python.on("close", (code) => {
  console.log(`Python process exited with code ${code}`);
  process.exit(code || 0);
});

python.on("error", (err) => {
  console.error("Failed to start python process:", err);
  process.exit(1);
});

// Forward signals
const signals: NodeJS.Signals[] = ["SIGINT", "SIGTERM", "SIGQUIT"];
signals.forEach((signal) => {
  process.on(signal, () => {
    if (!python.killed) {
      python.kill(signal);
    }
  });
});
