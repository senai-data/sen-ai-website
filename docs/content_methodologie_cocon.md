# Blueprint contenu « méthodologie » sen-ai (cocon GEO/EEAT sourcé)

> But : du contenu SEO/GEO sur sen-ai.fr qui **montre la science derrière le produit** (collecte → traitement → analyse), **bien sourcé**, **illustré par des captures du SaaS**. Issu d'un audit 4-agents du code (2026-06-27). Calqué sur la méthode cocon de storva (`docs/cocon-ressources.md`).

---

## 1. Fondations de recherche (le « spine » du contenu)

Ce sur quoi le produit est réellement construit. **À distinguer : sources externes citables vs heuristiques/benchmarks internes** (règle « toujours bien sourcer »).

### ✅ Sources externes vérifiables (citer librement)
| Brique produit | Étude / standard | Référence citable |
|---|---|---|
| **Page Audit GEO** + audit concurrents | Aggarwal et al., « GEO: Generative Engine Optimization », **KDD 2024** | `arXiv:2311.09735` |
| Lisibilité (pattern « easy to understand ») | **Flesch Reading Ease** | formule `206.835 − 1.015(mots/phrases) − 84.6(syllabes/mots)` |
| **Schema audit + générateur** | **schema.org** + Google Structured Data / Rich Results | schema.org spec |
| **Wikipedia entity** | Babbar/Wikidata + fréquence de citation | API publiques Wikipedia REST/Action + Wikidata (P31) |
| **Autorité (Babbar)** | **Babbar.tech** : DA=hostTrust, TF=domainTrust, CF=semanticValue, RD=backlinksCount | babbar.tech API |
| **EEAT / trust sources** | **Google E-E-A-T** (Quality Rater Guidelines) | Google Search Quality Evaluator Guidelines |
| **Conformité** | **EU AI Act**, Règlement (UE) 2024/1689, Art. 50 (limited-risk) ; RGPD Art. 33 | texte officiel |
| **Bandes de variance N-runs** | **Intervalle de confiance binomial 95%** : `1.96·√(p(1-p)/N)` | statistique standard |

### ⚠️ Sources à VÉRIFIER ou reformuler avant publication
- **« Stackmatix 30M citations study, mai 2026 » → ChatGPT cite Wikipedia 48%** (utilisé dans wikipedia.astro). Vérifier que l'étude existe publiquement et est citable ; sinon reformuler (« sur notre échantillon » ou retirer le chiffre exact).
- **« Fishkin / O'Donnell 2026 measurement study »** (cité dans `methodology.astro` pour la variance). **Rand Fishkin** est réel ; vérifier que cette étude 2026 existe et est citable. **Si non → reformuler en « nos propres benchmarks sur 50+ scans »** (déjà à moitié le cas). Ne pas inventer une étude.
- **« PDF SEO LLM Nov 2025 framework »** (cité dans `entity_analyzer.py` pour la typologie d'entités). Interne/non publié → présenter comme **notre cadre d'analyse**, pas comme une étude tierce.
- **Tout le reste des scorings** (internal linking, leverage scores, severity crise, composite scores) = **heuristiques maison**. Les présenter honnêtement comme « notre méthode de scoring », pas comme de la recherche externe.

> Règle d'or contenu : **une affirmation chiffrée = une source citable, ou alors c'est « notre mesure / notre méthode ».** Jamais « des études montrent » sans nom.

---

## 2. Le pipeline « comment on travaille » (collecte → traitement → analyse)

8 piliers. Pour chaque : ce qu'on collecte, comment on traite, la métrique, et l'**écran SaaS** (pour les captures).

### P1 — Mesurer la visibilité IA (le cœur)
- **Collecte** : mots-clés Google (HaloScan) → topics → personas → questions **brand-agnostic** (décrivent une situation d'achat, pas la marque) → on interroge **4 IA** (ChatGPT, Gemini, Claude, Mistral), **N fois** chacune (défaut N=10 payant).
- **Traitement** : génération topics/personas/questions = Claude Haiku (JSON strict). Tests = OpenAI (web_search) + Gemini (grounding). Extraction citations (CitationExtractor) + analyse d'entités (EntityAnalyzer, 5 types : brand/product/range/domain/expert_source). À N>1 : check regex par run + 1 passe consensus.
- **Analyse / métriques** : `brand_mention_rate` = mentions marque / total tests ×100 ; `domain_citation_rate` = citations domaine / total ×100 ; `avg_position` ; **bande de variance ±pts** (IC 95% binomial).
- **Écran** : `results.astro` (Overview), `topics`, `personas`, `questions`, `citations`.

### P2 — Page Audit GEO (Princeton)
- **Collecte** : **uniquement les pages que les IA citent déjà** (`citations[] où est_site_cible=true`), fetch HTTP poli + BeautifulSoup.
- **Traitement** : analyseur Princeton **7 patterns** — *statistics, cite sources, quotations* (poids **2.0×**, lift ~30-40%), *authoritative phrasing, fluency, easy-to-understand (Flesch), unique words* (1.0×).
- **Analyse** : `geo_score` 0-100 = moyenne pondérée des 7 (Σ score×poids / 10).
- **Écran** : `audit.astro`.

### P3 — Schema.org (audit + générateur)
- **Collecte** : mêmes pages citées ; parse `<script type=application/ld+json>` + microdata.
- **Traitement** : détecte le type de page (homepage/article/product/faq/about) ; vérifie les propriétés requises (schema.org spec) ; **génère** le JSON-LD manquant (heuristique pure, zéro LLM).
- **Analyse** : `schema_score` 0-100 (Organization 25 + schema page-type 25 + BreadcrumbList 20 + validité 20 + WebSite 10).
- **Écran** : `schema.astro`.

### P4 — Maillage interne
- **Collecte** : `<a href>` des pages citées.
- **Traitement** : classe interne/externe, ancres génériques (« cliquez ici »), vides, position (nav/footer/main).
- **Analyse** : `linking_score` 0-100 (qualité ancres 40 + diversité 30 + profondeur 15 + anti dead-end 15) + topologie (orphan/hub/dead-end).
- **Écran** : `internal-linking.astro`.

### P5 — Wikipedia / entité
- **Collecte** : API publiques Wikipedia (OpenSearch/REST/Action) + Wikidata.
- **Traitement** : 3 portes (similarité titre + signal description + Wikidata P31 marque/non-marque) + validation domaine dans les liens externes.
- **Analyse** : présence + `confidence` (match/low) + `quality_score` (longueur extrait + références + récence).
- **Écran** : `wikipedia.astro`.

### P6 — Autorité & EEAT off-site (concurrents, PR, YouTube, Reddit)
- **Collecte** : domaines/créateurs/threads **que les IA citent déjà** ; enrichissement **Babbar** (DA/TF/CF/RD), **LinkFinder** (prix), oEmbed YouTube, Haiku (sentiment Reddit, audience/voix média).
- **Traitement** : `leverage_score` (engagement log-citations + breadth concurrents + novelty + autorité/catalogue) ; classification competitor_only/shared/target_only ; matrice sentiment Reddit.
- **Analyse** : shortlist priorisée + badges autorité (tier-1 DA≥70, mid ≥40, niche <40).
- **Écran** : `competitors.astro`, `pr-outreach.astro`, `youtube.astro`, `reddit.astro`.

### P7 — Crise & sentiment (anti faux-positifs)
- **Collecte** : `brand_mentions[]` négatives par catégorie (sécurité/efficacité/ingrédients/prix/service…).
- **Traitement** : **Sentiment Judge** = Claude Haiku relit chaque mention négative et confirme/annule (négations mal lues). Severity composite.
- **Analyse** : `severity` 0-100 (volume 40 + ratio 30 + conséquence 15 + dispersion 15) ; crises partagées (industrie).
- **Écran** : `crisis.astro`.

### P8 — Actions + boucle de mesure T+14
- **Collecte** : opportunités par (question, provider) ; à T+14 post-publication, on **re-interroge les IA** sur la question ciblée.
- **Traitement** : 3 actions (FAQ si absent / netlinking si concurrents présents / content_update si en retard) ; génération article/FAQ (seo_llm) passée au **natural writing** (anti-tells IA, EEAT) ; mesure du **lift** de position avant/après publication.
- **Analyse** : `position_delta`, lift par provider, boost du media_catalog.
- **Écran** : `actions.astro` + kanban `content.astro`.

---

## 3. Garde-fous de COHÉRENCE (avec la page /methodology déjà publique)

La page publique `/methodology` (MAJ 2026-05-29) **affirme déjà** ces faits. Tout nouveau contenu **doit s'y conformer mot pour mot** :

- **N=10** par défaut (payant), bandes de variance ; OpenAI ~3-5%, Gemini ~10-15%, Claude ~3-5% ; IC à N=10 ≈ ±1-2 pts.
- **Modèles exacts** : GPT-5.4-mini / 5.4 ; Gemini 2.5 Flash/Pro (Google Ireland) ; Claude Haiku 4.5 / Sonnet 4.6 ; Mistral Large (EU, 41% FR). Nouveau provider ajouté au seuil 5% de part de recherche.
- **Questions brand-agnostic** (ne pas guider l'IA).
- **Classification marque pilotée par l'utilisateur, ZÉRO liste hardcodée** vertical/région/marque.
- **Limited-risk deployer** (AI Act Art. 50), **read-only**, pas d'entraînement, hébergement EU (Hetzner Falkenstein). 6 sous-traitants (Hetzner, OpenAI, Google Ireland, Anthropic, Stripe Europe, Babbar).
- **7 jours minimum** entre rescans, tendances en fenêtre glissante 30 j.
- **Ne JAMAIS contredire** : pas de « scraping massif », pas de « high-risk », pas de « on entraîne des modèles », pas de modèles périmés (GPT-4, Claude 3.5).

### 🔧 2 corrections à faire sur /methodology (et /fr) avant/pendant
1. **Sentiment Judge** : la page dit « Sprint candidate » → il est **LIVE**. À mettre à jour.
2. **Variance UI** : la page dit « planned » → vérifier le statut réel et aligner.

### 💡 3 opportunités (features réelles pas encore racontées publiquement)
- **Crise & severity** (formule volume/ratio/conséquence/dispersion) - puissant, jamais expliqué.
- **Boucle T+14** (publier → re-mesurer → lift → boost catalogue) - ferme la promesse « actions mesurables ».
- **Natural writing / EEAT** (6 règles + blacklist 55 mots) - explique pourquoi le contenu généré ne « sent » pas l'IA.

---

## 4. Cocon proposé (pilier → branches → feuilles)

**Pilier** : « Comment sen-ai mesure (vraiment) votre visibilité dans les IA » (la méthodologie, sourcée).

- **Branche A - Mesurer** : pourquoi 1 scan = du bruit (variance) · le paradigme N-runs (IC 95%) · les 4 IA et pourquoi · questions brand-agnostic · [capture Overview]
- **Branche B - Être cité (GEO on-page)** : l'étude Princeton KDD'24 et ses 7 patterns · statistiques/citations/quotes · schema.org · maillage · [captures Page Audit / Schema]
- **Branche C - Autorité & EEAT** : E-E-A-T expliqué · Babbar DA/TF/CF/RD · Wikipedia (la source la plus citée) · trust sources · [captures Competitors / Wikipedia]
- **Branche D - Off-site & achat média** : concurrents · PR/médias · YouTube · Reddit (sentiment) · **achat média raisonné** : quand un média cité par les LLM est trop cher, on propose une **alternative moins chère mais d'autorité/thématique équivalente** (scoring media_replacement) · [captures PR / YouTube / Reddit / modal Find-alternative]
- **Branche E - Agir & mesurer** : des actions priorisées · la boucle T+14 (lift) · crise & severity · contenu sans « tells » IA · [captures Actions / Crisis]
- **Branche G - Sur quels outils on s'appuie (crédibilité)** : on ne réinvente pas la roue, on branche la mesure de visibilité IA sur les **outils que les pros du SEO/netlinking utilisent déjà** :
  - **Babbar** (babbar.tech) → autorité de domaine (DA/TF/CF/RD)
  - **HaloScan** → mots-clés / positions Google FR (graine des topics & personas)
  - **YourTextGuru** → optimisation sémantique (scores SOSEO/DSEO) dans la génération d'article
  - **LinkFinder** → prix marché des médias (netlinking) pour l'alternative média
  Angle : « base solide et reconnue du marché » + ces 4 sont aussi dans le registre des **sous-traitants** (cohérence conformité). [captures où ces signaux apparaissent : Competitors badges DA, modal media, score article]

> Note conformité : si on met en avant LinkFinder / YourTextGuru / HaloScan dans le contenu public, vérifier qu'ils figurent au registre des sous-traitants `src/data/subprocessors.ts` (aujourd'hui seul **Babbar** y est). Soit les ajouter, soit préciser qu'ils ne traitent pas de donnée personnelle. Foot-gun #1 de la conformité S14.
- **Branche F - Confiance** : conformité AI Act (limited-risk) · données en EU · transparence par scan · [capture Compliance]

Chaque feuille = 1 intention, **answer-first** (réponse en gras en tête de H2), **sourcée** (1-3 liens d'autorité), **1-2 captures SaaS**, **CTA** vers /register. Réutiliser `shared/natural_writing/` + `docs/STYLE_GUIDE.md` (déjà en place).

---

## 4bis. Révéler vs protéger (anti-copie) - règle à appliquer à CHAQUE feuille

**Tension** : on veut être cité par les LLM (but GEO) ET ne pas livrer la notice de montage d'un clone. On ne peut pas « publier puis cacher aux LLM » (objectifs opposés). Le levier = **quoi on met dans le public**. La méthodo générale n'est pas le moat (Princeton public, concurrents déjà là) ; le moat = prompts + poids/formules + données accumulées + exécution + distribution.

| Niveau | Quoi | Où |
|---|---|---|
| ✅ **Publier** | principes, recherche citée, *ce qu'on mesure* + *pourquoi*, captures SaaS, bénéfices | cocon public (lisible LLM = le but) |
| 🟡 **Paraphraser** | « score 0-100 selon X facteurs » (jamais « poids=40, -5/position ») ; « on pondère autorité/fraîcheur/pertinence » sans la formule | public, flouté |
| 🔴 **Jamais public** | prompts exacts, poids/formules, code, modèle-par-tâche, coûts | code + **rapport transparence par scan (auth-gated)** |

Protection réelle = le **gate auth** (le détail fin vit dans le rapport par scan derrière login ; scrapers/LLM ne lisent que le public). À NE PAS faire : bloquer les crawlers IA sur le marketing (tue le GEO), cloaking (pénalisable + hypocrite pour une boîte GEO), publier une formule/prompt « pour faire pro » (= notice de clone).

## 4ter. Dogfooding : produire ET mesurer le cocon avec notre propre outil (preuve vivante)

Idée : utiliser le produit sur soi-même pour **prouver que le mécanisme marche - ou pas**.

- **Production (générateur d'article)** : scaffolder les feuilles via `generate_article` (SOSEO/DSEO + grounding + natural_writing + schema), mais en **DRAFT → édition humaine** (du machine-spun brut saboterait le message EEAT). Bon fit = feuilles **top-funnel éducatives** (cibles mots-clés) ; feuilles **produit/méthodo** = à la main (captures, voix, sources).
- **Mesure (boucle T+14) = le vrai trésor** : déclarer **sen-ai.fr comme son propre « client »**, suivre la marque « sen-ai » + questions GEO, publier le cocon, **mesurer à T+14/T+30** si les IA citent ces pages, et **afficher le résultat sur la page** (« produit par notre moteur, puis suivi : voici si les IA le citent »). Feuille flagship **« On a testé notre outil sur nous-mêmes »** (case study daté).
- **« ou pas » = feature** : publier ses propres ratés renforce la crédibilité d'une boîte de mesure. Mener avec le **résultat mesuré**, pas avec « c'est généré par IA ».
- Garde-fous : la grille **révéler/protéger §4bis** s'applique quel que soit l'auteur (humain ou générateur).

## 5. Infra à porter de storva (rappel)
`src/content.config.ts` (schéma Zod : title/description/parent/branch/priority/related/lexical/sources/faq/updated/draft/order), route `/ressources/[...slug].astro` (ou `/methodologie/` ou `/guides/`), hub `index.astro`, layout (Article+BreadcrumbList+FAQPage JSON-LD), composant maillage `PagesLiees`, composant CTA, composant capture produit, `sitemap.xml.ts`. Lint anti-tells déjà présent côté sen-ai.

> ⚠️ **Beacon Web Analytics** : le nouveau layout d'article du cocon devra **réajouter le beacon Cloudflare** (1 ligne avant `</head>`, cf [[project_cloudflare_web_analytics]]) OU réutiliser `BaseLayout`, sinon les pages contenu ne remontent pas dans les stats.
