// @ts-check
// The project website: German first, English prepared (pages without a translation show the German text with a
// note). The API reference comes from the app's OpenAPI document, the MCP tools from the server itself; both are
// written by tools/docs_data.py and checked by test_docs_data.py.
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightOpenAPI, { createOpenAPISidebarGroup } from 'starlight-openapi';

const REPO = 'https://github.com/sobrino89/mtdl-web';
const apiReference = createOpenAPISidebarGroup();

export default defineConfig({
  site: 'https://sobrino89.github.io',
  base: '/mtdl-web',
  trailingSlash: 'always',
  // Pages show the templates from the repository root (compose.yaml, .env.example) as they are; the dev server
  // may read them there.
  vite: { server: { fs: { allow: ['..'] } } },
  integrations: [
    starlight({
      title: 'mtdl-web',
      description:
        'Mediatheken der öffentlich-rechtlichen Sender durchsuchen, herunterladen und Serien abonnieren, selbst gehostet.',
      logo: { src: './src/assets/logo.svg' },
      favicon: '/favicon.svg',
      defaultLocale: 'root',
      locales: {
        root: { label: 'Deutsch', lang: 'de' },
        en: { label: 'English', lang: 'en' },
      },
      social: [{ icon: 'github', label: 'GitHub', href: REPO }],
      editLink: { baseUrl: `${REPO}/edit/main/website/` },
      customCss: ['./src/styles/theme.css'],
      plugins: [
        starlightOpenAPI([
          {
            base: 'api',
            schema: './src/data/openapi.json',
            sidebar: { group: apiReference, label: 'API-Referenz', operations: { badges: true } },
          },
        ]),
      ],
      sidebar: [
        {
          label: 'Erste Schritte',
          translations: { en: 'Getting started' },
          items: ['getting-started/introduction', 'getting-started/quickstart'],
        },
        {
          label: 'Installation',
          translations: { en: 'Installation' },
          items: [
            'install/docker-compose',
            'install/unraid',
            'install/environment',
            'install/folders',
            'install/access',
            'install/reverse-proxy',
            'install/updates',
          ],
        },
        {
          label: 'Bedienung',
          translations: { en: 'Usage' },
          items: [
            'usage/first-run',
            'usage/search',
            'usage/subscriptions',
            'usage/downloads',
            'usage/naming',
            'usage/libraries',
            'usage/takeover',
          ],
        },
        {
          label: 'Integrationen',
          translations: { en: 'Integrations' },
          items: [
            'integrations/rest-api',
            apiReference,
            'integrations/webhooks',
            'integrations/hooks',
            'integrations/mcp',
            'integrations/mcp-tools',
          ],
        },
        {
          label: 'Hintergrund',
          translations: { en: 'Background' },
          items: ['background/episodes', 'background/data-sources', 'background/security'],
        },
        {
          label: 'Projekt',
          translations: { en: 'Project' },
          items: ['project/about', 'project/faq', 'project/contributing', 'project/license'],
        },
      ],
    }),
  ],
});
