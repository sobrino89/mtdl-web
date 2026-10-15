// Checks every internal link of the built site (dist/): the page exists and so does the anchor it points at.
// Run after `npm run build`: node check-links.mjs. External links are not fetched.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const DIST = new URL('./dist/', import.meta.url).pathname;
const BASE = '/mtdl-web/';

function pages(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? pages(path) : name.endsWith('.html') ? [path] : [];
  });
}

const ids = new Map();
function idsOf(file) {
  if (!ids.has(file)) {
    const html = readFileSync(file, 'utf8');
    ids.set(file, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
  }
  return ids.get(file);
}

function target(pathname) {
  if (!pathname.startsWith(BASE)) return null;
  const rest = decodeURIComponent(pathname.slice(BASE.length));
  const file = join(DIST, rest.endsWith('/') || rest === '' ? join(rest, 'index.html') : rest);
  return existsSync(file) ? file : null;
}

const problems = [];
let links = 0;
for (const file of pages(DIST)) {
  const url = new URL(`https://example.org${BASE}${relative(DIST, file).replace(/index\.html$/, '')}`);
  const html = readFileSync(file, 'utf8');
  for (const [, href] of html.matchAll(/\s(?:href|src)="([^"]+)"/g)) {
    if (/^(?:[a-z]+:|\/\/)/i.test(href) && !href.startsWith('https://example.org')) continue;
    const resolved = new URL(href.replaceAll('&amp;', '&'), url);
    links += 1;
    const page = resolved.pathname === url.pathname ? file : target(resolved.pathname);
    if (page === null) {
      problems.push(`${relative(DIST, file)}: ${href} (missing page)`);
    } else if (resolved.hash && page.endsWith('.html') && !idsOf(page).has(decodeURIComponent(resolved.hash.slice(1)))) {
      problems.push(`${relative(DIST, file)}: ${href} (missing anchor)`);
    }
  }
}

console.log(`${links} internal links checked`);
if (problems.length) {
  console.log([...new Set(problems)].join('\n'));
  process.exit(1);
}
