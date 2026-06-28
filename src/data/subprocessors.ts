/**
 * Sub-processors registry + changelog — single source of truth for AI Act
 * / GDPR disclosure surfaces (per-scan compliance, org compliance hub,
 * public methodology page).
 *
 * Edit this file when :
 *   1. Adding a new sub-processor : append to SUBPROCESSORS with
 *      `added_on` = the date the provider went live in production,
 *      AND add a matching CHANGELOG entry with kind='added'.
 *   2. Sunsetting one : flip `status` to 'sunset', set `sunset_on`,
 *      and add a CHANGELOG entry with kind='sunset'.
 *   3. Changing the scope / purpose / hosting region : update the
 *      record AND add a CHANGELOG entry with kind='scope_change'.
 *
 * The git log on this file is the immutable audit trail. The rendered
 * CHANGELOG list is the user-visible counterpart.
 */

export interface SubProcessor {
  id: string;
  name: string;
  legal_entity: string;
  category: 'ai-provider' | 'infrastructure' | 'data-enrichment' | 'payments';
  purpose: string;
  hosting_region: string;
  transfer_mechanism: string;
  /** Populated only for AI providers - surfaced in per-scan compliance table. */
  model_family?: string;
  added_on: string;
  status: 'active' | 'sunset';
  sunset_on?: string;
}

export interface ChangelogEntry {
  date: string;
  kind: 'added' | 'scope_change' | 'sunset' | 'restored' | 'initial_disclosure';
  subprocessor_id: string;
  summary: string;
}

/**
 * Current active sub-processors used by sen-ai.fr. Order matters - rendered
 * in this order on the compliance hub table.
 */
export const SUBPROCESSORS: SubProcessor[] = [
  {
    id: 'hetzner',
    name: 'Hetzner Online GmbH',
    legal_entity: 'Hetzner Online GmbH',
    category: 'infrastructure',
    purpose: 'Application hosting, PostgreSQL database, file storage',
    hosting_region: 'Helsinki, Finland (EU)',
    transfer_mechanism: 'EU controller, no transfer',
    added_on: '2026-04-01',
    status: 'active',
  },
  {
    id: 'openai',
    name: 'OpenAI ChatGPT',
    legal_entity: 'OpenAI, L.L.C.',
    category: 'ai-provider',
    purpose: 'AI provider (ChatGPT) - read-only inference, no training',
    hosting_region: 'United States',
    transfer_mechanism: 'EU-US Data Privacy Framework + SCC',
    model_family: 'GPT-5.4-mini / GPT-5.4',
    added_on: '2026-04-01',
    status: 'active',
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    legal_entity: 'Google Ireland Ltd',
    category: 'ai-provider',
    purpose: 'AI provider (Gemini) - read-only inference, no training',
    hosting_region: 'European Union (Belgium / Netherlands)',
    transfer_mechanism: 'EU controller, no transfer',
    model_family: 'Gemini 2.5 Flash / 2.5 Pro',
    added_on: '2026-04-01',
    status: 'active',
  },
  {
    id: 'anthropic',
    name: 'Anthropic Claude',
    legal_entity: 'Anthropic PBC',
    category: 'ai-provider',
    purpose: 'AI provider (Claude) - read-only inference, no training. Used for structured-JSON extraction (brand mention parser, sentiment judge) and premium-quality runs.',
    hosting_region: 'United States',
    transfer_mechanism: 'EU-US Data Privacy Framework + SCC',
    model_family: 'Claude Haiku 4.5 / Sonnet 4.6',
    added_on: '2026-04-01',
    status: 'active',
  },
  {
    id: 'stripe',
    name: 'Stripe Payments Europe',
    legal_entity: 'Stripe Payments Europe Ltd',
    category: 'payments',
    purpose: 'Billing & payment processing',
    hosting_region: 'European Union (Ireland)',
    transfer_mechanism: 'EU controller, no transfer',
    added_on: '2026-04-01',
    status: 'active',
  },
  {
    id: 'babbar',
    name: 'Babbar Technologies',
    legal_entity: 'Babbar Technologies SAS',
    category: 'data-enrichment',
    purpose: 'Backlink & domain authority enrichment (media outreach feature)',
    hosting_region: 'European Union (France)',
    transfer_mechanism: 'EU controller, no transfer',
    added_on: '2026-04-15',
    status: 'active',
  },
  {
    id: 'haloscan',
    name: 'Haloscan',
    legal_entity: 'ARCHI301 (RCS Nice 950 897 371)',
    category: 'data-enrichment',
    purpose: 'Keyword & SERP data (Google France) - seeds scan topics and personas. Processes search keywords and domains, not personal data.',
    hosting_region: 'European Union (France, OVH)',
    transfer_mechanism: 'EU controller, no transfer',
    added_on: '2026-06-28',
    status: 'active',
  },
  {
    id: 'yourtextguru',
    name: 'YourTextGuru',
    legal_entity: 'Babbar Technologies SAS',
    category: 'data-enrichment',
    purpose: 'Semantic optimization scoring (SOSEO / DSEO) during content generation. Processes content keywords, not personal data.',
    hosting_region: 'European Union (France)',
    transfer_mechanism: 'EU controller, no transfer',
    added_on: '2026-06-28',
    status: 'active',
  },
  {
    id: 'linkfinder',
    name: 'Link Finder',
    legal_entity: 'Apexx LLC (United States)',
    category: 'data-enrichment',
    purpose: 'Netlinking price comparison for the media-alternative feature. Processes media domains and market prices, not personal data.',
    hosting_region: 'Germany (Contabo) - publisher US-incorporated',
    transfer_mechanism: 'SCC (US publisher)',
    added_on: '2026-06-28',
    status: 'active',
  },
];

/**
 * Audit-trail changelog. Newest entries should be appended at the top.
 * The `initial_disclosure` kind is reserved for the AI Act compliance pack
 * ship date - everything that existed at that point gets one entry.
 */
export const CHANGELOG: ChangelogEntry[] = [
  {
    date: '2026-06-28',
    kind: 'scope_change',
    subprocessor_id: 'hetzner',
    summary: 'Correction de la region d hebergement Hetzner : Helsinki, Finlande (precedemment libelle Falkenstein, Allemagne par erreur). Aucun changement reel : l infrastructure est et reste dans l Union europeenne.',
  },
  {
    date: '2026-06-28',
    kind: 'added',
    subprocessor_id: 'haloscan',
    summary: 'Haloscan ajoute au registre : donnees mots-cles et SERP Google France, amorce des topics et personas. Integration SEO existante, traite des mots-cles et domaines, pas de donnee personnelle.',
  },
  {
    date: '2026-06-28',
    kind: 'added',
    subprocessor_id: 'yourtextguru',
    summary: 'YourTextGuru ajoute au registre : scores semantiques SOSEO et DSEO pour la generation de contenu. Edite par Babbar Technologies SAS, traite des mots-cles de contenu, pas de donnee personnelle.',
  },
  {
    date: '2026-06-28',
    kind: 'added',
    subprocessor_id: 'linkfinder',
    summary: 'Link Finder ajoute au registre : comparateur de prix du netlinking pour la fonctionnalite alternative media. Editeur Apexx LLC (Etats-Unis), hebergement Contabo (Allemagne), traite des domaines et prix, pas de donnee personnelle.',
  },
  {
    date: '2026-05-29',
    kind: 'initial_disclosure',
    subprocessor_id: 'all',
    summary: 'Initial AI Act compliance pack published. Sub-processors registry frozen at 6 entries (Hetzner, OpenAI, Google Ireland, Anthropic, Stripe Europe, Babbar).',
  },
];

export function getSubprocessor(id: string): SubProcessor | undefined {
  return SUBPROCESSORS.find(sp => sp.id === id);
}

export function activeSubprocessors(): SubProcessor[] {
  return SUBPROCESSORS.filter(sp => sp.status === 'active');
}

/**
 * Pretty label for a changelog kind. Used by the rendered changelog table.
 */
export function changelogKindLabel(kind: ChangelogEntry['kind']): string {
  switch (kind) {
    case 'added':              return 'Added';
    case 'scope_change':       return 'Scope change';
    case 'sunset':             return 'Sunset';
    case 'restored':           return 'Restored';
    case 'initial_disclosure': return 'Initial disclosure';
  }
}

export function changelogKindClass(kind: ChangelogEntry['kind']): string {
  switch (kind) {
    case 'added':              return 'bg-emerald-50 text-emerald-700';
    case 'scope_change':       return 'bg-amber-50 text-amber-700';
    case 'sunset':             return 'bg-red-50 text-red-700';
    case 'restored':           return 'bg-blue-50 text-blue-700';
    case 'initial_disclosure': return 'bg-gray-100 text-charcoal-light';
  }
}
