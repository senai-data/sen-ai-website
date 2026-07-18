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
  // Sitemap = the list of pages we ASK Google to index. Anything a crawler
  // cannot or must not reach is noise that burns crawl budget and shows up in
  // Search Console as "Blocked by robots.txt" / "Page with redirect".
  //
  // 2026-07-18 : the sitemap carried 75 URLs of which 30 were junk - 22
  // `/app/**` routes (auth-gated AND Disallow'd in robots.txt, so Google was
  // being pointed at pages it is forbidden to fetch), plus the auth and
  // transactional pages. Down to the ~45 genuinely indexable ones.
  integrations: [sitemap({
    filter: (page) => {
      const path = new URL(page).pathname;
      // Mirrors public/robots.txt - keep the two in sync.
      if (path.startsWith('/app/') || path.startsWith('/api/') || path.startsWith('/r/')) return false;
      // Auth + transactional : no search value, and indexing them is a leak.
      const PRIVATE = [
        '/login/', '/register/', '/forgot-password/', '/reset-password/',
        '/verify-email/', '/welcome/', '/dashboard/', '/audit/confirm/',
      ];
      if (PRIVATE.includes(path)) return false;
      // 301 kept alive for an old backlink - a redirect is not a canonical URL.
      if (path.includes('/ressources/frequence-posts-google')) return false;
      return true;
    },
  })]
});
