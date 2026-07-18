// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';
import node from '@astrojs/node';

export default defineConfig({
  site: 'https://sen-ai.fr',
  output: 'static',
  adapter: node({ mode: 'standalone' }),
  vite: {
    plugins: [tailwindcss()]
  },
  // Keep redirect-only and app routes out of the sitemap : a 301 listed as a
  // canonical URL is noise for crawlers (2026-07-18, /ressources/frequence-
  // posts-google/ is a 301 kept alive for an old backlink).
  integrations: [sitemap({
    filter: (page) => !page.includes('/ressources/frequence-posts-google'),
  })]
});
