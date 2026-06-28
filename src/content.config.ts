import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Cocon sémantique "visibilité IA" (/guides). Chaque .md = 1 page = 1 intention.
// L'arbre (mère -> intermédiaires -> filles) est porté par `parent` ; le maillage auto
// (fil d'Ariane, pages liées) se calcule depuis ces champs. Slugs FR, plats.
// Porté du cocon de storva (docs/cocon_plan_redaction.md), adapté .md (pas de dépendance MDX).
const guides = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/guides' }),
  schema: z.object({
    title: z.string(),                 // H1 + <title>
    description: z.string(),           // meta description + accroche
    parent: z.string().optional(),     // id de la page parente ; absent = pilier (racine)
    branch: z.string().optional(),     // libellé court de la branche (pour le hub + fil d'Ariane)
    priority: z.enum(['coeur', 'ext1', 'ext2']).default('ext2'),
    related: z.array(z.string()).default([]),  // ids de pages liées (feuilles)
    lexical: z.array(z.string()).default([]),  // champ lexical à couvrir (note de rédaction)
    sources: z.array(z.object({ label: z.string(), url: z.string().url() })).default([]),
    faq: z.array(z.object({ q: z.string(), a: z.string() })).default([]),  // bloc FAQ + schema FAQPage (GEO)
    cta: z.object({ titre: z.string(), texte: z.string().optional(), label: z.string().default('Lancer mon scan gratuit'), href: z.string().default('/register') }).optional(),
    updated: z.string().optional(),    // date ISO de dernière mise à jour
    draft: z.boolean().default(false), // exclu du rendu si true
    order: z.number().default(100),    // ordre d'affichage dans le hub / listes
  }),
});

export const collections = { guides };
