#!/usr/bin/env node
/**
 * Cross-platform runner for Railway CLI with Infrastructure as Code.
 * Solves Windows PATH / execFileSync ENOENT compatibility with railway/iac SDK.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const isWindows = process.platform === "win32";
const localCliExe = path.resolve("node_modules/@railway/cli/bin/railway.exe");

let executable = "railway";
if (isWindows && fs.existsSync(localCliExe)) {
  executable = localCliExe;
  process.env._ = localCliExe;
}

const args = process.argv.slice(2);
const result = spawnSync(executable, args, {
  stdio: "inherit",
  env: process.env,
});

process.exit(result.status ?? 0);
