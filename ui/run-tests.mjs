// Runs test/*.test.ts(x) with node's test runner: esbuild bundles each file (TypeScript, JSX, bundler-style imports)
// into a temporary directory first, so the tests see the same module resolution as the app.
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const files = readdirSync("test").filter((name) => /\.test\.tsx?$/.test(name));
if (!files.length) {
  console.error("no tests in test/");
  process.exit(1);
}
const out = mkdtempSync(join(tmpdir(), "mtdl-ui-test-"));
try {
  await build({
    entryPoints: files.map((name) => join("test", name)),
    bundle: true,
    platform: "node",
    format: "esm",
    target: ["node22"],
    jsx: "automatic",
    jsxImportSource: "preact",
    outdir: out,
    outExtension: { ".js": ".mjs" },
    logLevel: "warning",
  });
  const bundles = files.map((name) => join(out, name.replace(/\.tsx?$/, ".mjs")));
  const run = spawnSync(process.execPath, ["--test", "--test-reporter=spec", ...bundles], { stdio: "inherit" });
  process.exitCode = run.status ?? 1;
} finally {
  rmSync(out, { recursive: true, force: true });
}
