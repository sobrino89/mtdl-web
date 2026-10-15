// Every section of .env.example appears on the environment page exactly once, and the page names no section the
// template lacks: the tables there come from the template, so a missing one would hide variables silently.
// Run with the other checks after the build: npm run check.
import { readFileSync } from 'node:fs';
import { envSections } from './src/env-sections.mjs';

const example = readFileSync(new URL('../.env.example', import.meta.url), 'utf8');
const page = readFileSync(new URL('./src/content/docs/install/environment.mdx', import.meta.url), 'utf8');

const sections = [...envSections(example).keys()];
const used = [...page.matchAll(/<EnvTable section="([^"]+)" \/>/g)].map((m) => m[1]);
const problems = [
  ...sections.filter((s) => !used.includes(s)).map((s) => `section "${s}" of .env.example is not on the page`),
  ...used.filter((s) => !sections.includes(s)).map((s) => `the page names section "${s}", .env.example has none`),
  ...used.filter((s, i) => used.indexOf(s) !== i).map((s) => `section "${s}" appears twice`),
];

console.log(`${sections.length} sections of .env.example checked`);
if (problems.length) {
  console.log(problems.join('\n'));
  process.exit(1);
}
