// Stylesheets are bundled by esbuild; TypeScript only needs to know they exist.
declare module "*.css";

// The light build of hls.js (no subtitles, no DRM) has the API of the full one but no declaration file of its own.
declare module "hls.js/light" {
  export { default } from "hls.js";
  export * from "hls.js";
}
