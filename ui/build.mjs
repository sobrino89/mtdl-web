// Bundles the UI into ../src/mtdl_web/ui (served by the backend at /); no external resources at runtime.
import { build } from "esbuild";
import { copyFileSync, mkdirSync, rmSync } from "node:fs";

const out = process.env.MTDL_UI_OUT ?? "../src/mtdl_web/ui";
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
await build({
  entryPoints: { app: "src/main.tsx" },
  bundle: true,
  minify: true,
  sourcemap: true,
  format: "esm",
  target: ["es2022"],
  jsx: "automatic",
  jsxImportSource: "preact",
  outdir: out,
  splitting: true, // hls.js lands in its own chunk, loaded only for an HLS preview
  chunkNames: "chunks/[name]-[hash]",
  loader: { ".css": "css" },
  legalComments: "linked",
  logLevel: "info",
});
copyFileSync("src/index.html", `${out}/index.html`);

// Licenses of the bundled packages ship with the bundle (Preact MIT, hls.js Apache-2.0).
mkdirSync(`${out}/licenses`, { recursive: true });
for (const pkg of ["preact", "hls.js"]) copyFileSync(`node_modules/${pkg}/LICENSE`, `${out}/licenses/${pkg}.txt`);

// The API documentation (/api/docs) with Swagger UI from the pinned npm package instead of a CDN.
const docs = `${out}/api-docs`;
mkdirSync(docs, { recursive: true });
for (const file of ["swagger-ui-bundle.js", "swagger-ui.css", "LICENSE", "NOTICE"]) {
  copyFileSync(`node_modules/swagger-ui-dist/${file}`, `${docs}/${file}`);
}
