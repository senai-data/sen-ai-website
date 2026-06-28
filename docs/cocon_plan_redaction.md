# Cocon contenu sen-ai - plan de rédaction (briefs feuille par feuille)

> Compagnon de `content_methodologie_cocon.md` (qui porte la stratégie, les sources vérifiées §1bis/§1ter, la grille révéler/protéger, le dogfooding). Ici = **l'arborescence + 1 brief par feuille**, prête à rédiger.
> Langue : **FR** (contenu) ; interface produit EN. Style : `docs/STYLE_GUIDE.md` + `shared/natural_writing/` (answer-first, sourcé, zéro em-dash, zéro tell IA).
> Légende tier : ✅ publier · 🟡 paraphraser (jamais le chiffre/poids exact) · 🔴 jamais public.
> Chaque feuille = **1 intention** ; ouvre par une réponse en gras (answer-first = cohérent avec le query fan-out) ; 1-3 sources citables ; 1-2 captures SaaS ; CTA `/register`.

---

## ✅ Statut de rédaction (2026-06-28) - COCON COMPLET (30 pages live, hors flagship)

Toutes les branches sont rédigées, déployées et vérifiées en 200 sur `https://sen-ai.fr/guides/`. Build local vert avant chaque lot, zéro em-dash, maillage auto + croisé, fil d'Ariane + FAQPage + beacon Cloudflare présents.

| Branche | Statut | Pages (slugs) |
|---|---|---|
| Pilier | ✅ | `methodologie-visibilite-ia` |
| A - Mesurer | ✅ | `mesurer-visibilite-ia` + `un-seul-test-chatgpt-ne-suffit-pas`, `paradigme-n-runs`, `quelles-ia-suivre-marche-francais`, `questions-sans-marque-eviter-biais` |
| B - Être cité | ✅ | `etre-cite-par-les-ia` + `query-fan-out`, `etude-princeton-geo`, `contenu-extractible-stats-citations-quotes`, `schema-org-et-ia`, `cocon-semantique-et-maillage` |
| C - Autorité & E-E-A-T | ✅ | `autorite-eeat-ia` + `eeat-explique`, `wikipedia-source-plus-citee-chatgpt`, `autorite-domaine-da-tf-cf-rd` |
| D - Off-site & média | ✅ | `off-site-et-achat-media` + `concurrents-cites-par-ia`, `medias-cites-par-ia-relations-presse`, `reddit-youtube-sources-ugc-ia`, `media-cite-trop-cher-alternative` |
| E - Agir & mesurer | ✅ | `agir-et-mesurer` + `actions-priorisees`, `boucle-t14-mesurer-le-lift`, `radar-de-crise`, `contenu-qui-ne-sent-pas-lia` |
| F - Confiance | ✅ | `confiance` + `conformite-ai-act-limited-risk`, `donnees-en-europe` |
| G - Outils du marché | ✅ | `outils-seo-sur-lesquels-on-sappuie` (enfant direct du pilier ; 1 feuille = pas d'intermédiaire) |
| FLAGSHIP | ⏳ différé | `on-a-teste-notre-outil-sur-nous-memes` - a besoin d'un **vrai lift mesuré** ; à écrire à T+14 (~12/07/2026) une fois sen-ai.fr déclaré comme son propre client et le cocon remesuré. Pas de chiffre inventé. |

**Sources confirmées en cours de rédaction** : Link Finder (`link-finder.net/fr/`) vérifié = comparateur de prix netlinking FR (cadré comparateur, pas marketplace).

**TODO restants (hors rédaction)** :
- **Conformité** : HaloScan / YourTextGuru / Link Finder sont mis en avant (feuille G) mais absents de `src/data/subprocessors.ts` (seul Babbar y figure). Cadrage actuel dans la feuille = « signaux SEO publics, pas de donnée personnelle ». À acter ou compléter par un ajout au registre.
- **Captures SaaS** : non insérées. `GuideLayout` n'a pas de composant image - à câbler le jour où on dépose des PNG.

---

## PILIER (page mère)
**`/methodologie/` - « Comment sen-ai mesure (vraiment) votre visibilité dans les IA »**
- Intention : poser le problème (les IA citent certaines marques, pas d'autres ; c'est le nouveau SEO) + cartographier les 6 branches.
- Sources : Google AI Mode blog (query fan-out) ; SQRG ; Aggarwal KDD 2024.
- Capture : Overview d'un scan (grade + taux de citation par IA).
- Maillage : liens vers les 6 branches. Tier ✅.

---

## BRANCHE A - MESURER (variance & N-runs)
*Page intermédiaire `/methodologie/mesurer-visibilite-ia/` : « Mesurer sa présence dans ChatGPT, Gemini, Claude, Mistral ».*

| Feuille (slug) | Intention / query cible | Sous-intents (fan-out) | Sources citables | Capture SaaS | Tier |
|---|---|---|---|---|---|
| `un-seul-test-chatgpt-ne-suffit-pas` | « est-ce fiable de tester ma marque 1 fois sur ChatGPT ? » | non-déterminisme, variance par run, par jour | **SparkToro Fishkin/O'Donnell 27/01/2026** (2 961 runs, <1/100 même liste) ; arXiv:2603.08924, 2604.07585 | Overview + bande variance ±pts | ✅ |
| `paradigme-n-runs` | « comment mesurer une visibilité IA fiable ? » | échantillonnage, distribution, IC | IC binomial 95% ; methodology §2 (N=10) | KPI « X% ± Y pts » | ✅ (🟡 sur la formule exacte) |
| `quelles-ia-suivre-marche-francais` | « quelles IA suivre pour la France ? » | ChatGPT/Gemini/Claude/Mistral, hébergement EU | methodology §3 ; Mistral 41% trafic FR | Filtre provider | ✅ |
| `questions-sans-marque-eviter-biais` | « comment tester sans guider l'IA ? » | questions brand-agnostic, personas | methodology §4 | Onglet Questions | ✅ |

---

## BRANCHE B - ÊTRE CITÉ (GEO on-page)
*Intermédiaire `/methodologie/etre-cite-par-les-ia/` : « Le contenu qui se fait citer ».*

| Feuille | Intention / query | Sous-intents | Sources | Capture | Tier |
|---|---|---|---|---|---|
| `query-fan-out` | « c'est quoi le query fan-out ? » | décomposition en sous-requêtes, retrieval passage-level | **Brevet Google `US20240289407A1`** + blog AI Mode (I/O 2025) | Topics + Personas (= sous-intentions) | ✅ |
| `etude-princeton-geo` | « comment optimiser son contenu pour les IA ? » | les familles de signaux, l'effet varie selon le domaine | **Aggarwal et al., KDD 2024** (arXiv:2311.09735) - **« jusqu'à 40%, max du papier, contesté »** ; le solide = « varie selon le domaine » | Page Audit (score 0-100) | ✅ (🔴 jamais les poids ; 🔴 jamais « +40% » comme moyenne) |
| `contenu-extractible-stats-citations-quotes` | « quel contenu se fait citer ? » | statistiques, sources, citations d'experts, lisibilité | Princeton (tactiques) ; Flesch | Page Audit patterns | ✅ (🟡 seuils) |
| `schema-org-et-ia` | « le schema.org aide-t-il pour les IA ? » | éligibilité vs citation | **Google AI-features doc** (« no special schema required ») ; éligibilité ≠ part de citation | Onglet Schema | ✅ (honnête : ne pas survendre) |
| `cocon-semantique-et-maillage` | « c'est quoi un cocon sémantique ? » | pyramide de silos, vases communicants, page = 1 intention | **Laurent Bourrelly** (laurentbourrelly.com/blog/1631.php) - inventeur du cocon nommé | Internal linking | ✅ |

---

## BRANCHE C - AUTORITÉ & E-E-A-T
*Intermédiaire `/methodologie/autorite-eeat-ia/`.*

| Feuille | Intention / query | Sous-intents | Sources | Capture | Tier |
|---|---|---|---|---|---|
| `eeat-explique` | « c'est quoi l'E-E-A-T ? » | Experience/Expertise/Authoritativeness/Trust, Trust au centre | **Google « General Guidelines » (SQRG, rév. 11/09/2025)** ⚠️ pas un facteur de ranking direct | - | ✅ |
| `wikipedia-source-plus-citee-chatgpt` | « pourquoi une page Wikipedia compte ? » | présence entité, notoriété | **Profound, 680M citations** (Wikipedia = domaine n°1 ChatGPT, 47,9% du top-10) | Onglet Wikipedia | ✅ |
| `autorite-domaine-da-tf-cf-rd` | « c'est quoi l'autorité de domaine ? » | backlinks, trust flow, citation flow | **Babbar** (à sourcer, pass #3) | Badges DA Concurrents | ✅ (🔴 jamais nos poids de scoring) |

---

## BRANCHE D - OFF-SITE & ACHAT MÉDIA

| Feuille | Intention / query | Sous-intents | Sources | Capture | Tier |
|---|---|---|---|---|---|
| `concurrents-cites-par-ia` | « pourquoi mes concurrents sont cités et pas moi ? » | reverse-engineering, pages gagnantes | (heuristique maison = « notre méthode ») | Onglet Concurrents | ✅ (🟡) |
| `medias-cites-par-ia-relations-presse` | « quels médias citent mon secteur dans l'IA ? » | PR, co-citation, attribution gap | **Strauss et al. arXiv:2508.00838** (récupéré ≠ cité) | Onglet PR/Media | ✅ |
| `reddit-youtube-sources-ugc-ia` | « pourquoi Reddit/YouTube sortent dans l'IA ? » | UGC, sentiment communautaire | **Profound** (Reddit n°1 sur Perplexity + Google AIO) | Onglets Reddit/YouTube | ✅ |
| `media-cite-trop-cher-alternative` | « comment acheter un article au bon prix ? » | prix marché, autorité équivalente | **LinkFinder** (prix, à sourcer pass #3) | Modal Find-alternative | ✅ (🔴 jamais les poids du scoring) |

---

## BRANCHE E - AGIR & MESURER

| Feuille | Intention / query | Sous-intents | Sources | Capture | Tier |
|---|---|---|---|---|---|
| `actions-priorisees` | « comment améliorer concrètement ma visibilité IA ? » | FAQ / netlinking / mise à jour | (méthode maison) | Onglet Actions | ✅ (🟡) |
| `boucle-t14-mesurer-le-lift` | « comment savoir si mon contenu a marché ? » | re-mesure post-publication, lift par IA | (méthode maison + non-déterminisme = pourquoi re-mesurer) | Delta de position | ✅ |
| `radar-de-crise` | « comment surveiller ma réputation dans l'IA ? » | signaux négatifs, severity, sentiment | (méthode maison ; Sentiment Judge anti faux-positif) | Onglet Crisis | ✅ (🔴 formule severity) |
| `contenu-qui-ne-sent-pas-lia` | « comment écrire du contenu non détectable IA ? » | tells IA, EEAT, naturel | `docs/STYLE_GUIDE.md` (méthode maison) | - | ✅ (🔴 la blacklist complète) |

---

## BRANCHE F - CONFIANCE

| Feuille | Intention / query | Sources | Capture | Tier |
|---|---|---|---|---|
| `conformite-ai-act-limited-risk` | « sen-ai est-il conforme à l'AI Act ? » | **Règlement (UE) 2024/1689**, Art. 50 ; methodology §8 | Onglet Compliance | ✅ |
| `donnees-en-europe` | « mes données restent-elles en UE ? » | Hetzner Falkenstein, sous-traitants | Page Compliance org | ✅ |

---

## BRANCHE G - OUTILS DU MARCHÉ *(bloquée tant que pass #3 non fait)*
| Feuille | Intention | Sources (à obtenir pass #3) | Tier |
|---|---|---|---|
| `outils-seo-sur-lesquels-on-sappuie` | « sur quels outils sen-ai s'appuie ? » | sites officiels : **Babbar** (Sylvain Peyronnet/exensa), **HaloScan**, **YourTextGuru** (SOSEO/DSEO), **LinkFinder** | ✅ + ⚠️ vérifier registre sous-traitants |

---

## FLAGSHIP (Peak-End, dogfooding)
**`/methodologie/on-a-teste-notre-outil-sur-nous-memes/`**
- Intention : preuve vivante. « Cet article a été produit par notre moteur, puis suivi. Voici si les IA citent sen-ai aujourd'hui. »
- Mécanique : sen-ai.fr déclaré comme son propre « client » → scans GEO → **lift mesuré à T+14/T+30**, publié et **daté** sur la page (« ou pas ! » assumé). + attribution `signup_referrer` (contenu→inscription→Stripe).
- Sources : la page consomme ses propres données produit. Capture : Overview de « sen-ai ».
- Tier ✅ (mener avec le **résultat mesuré**, pas « c'est généré par IA »).

---

## Récap volumétrie
1 pilier + 5 pages intermédiaires + **~22 feuilles** + 1 flagship. Zéro orphelin (chaque feuille a un parent + des frères/related). Ordre de rédaction conseillé : **Flagship + Branche A** (la variance = ton USP, le mieux sourcé) → B → C → D/E → F → G (après pass #3).

## À faire avant rédaction
1. **Pass #3** (court) : sourcer le stack FR (sites officiels Babbar/HaloScan/YourTextGuru/LinkFinder) → débloque branche G + feuilles C3/D4.
2. **Porter l'infra cocon** de storva (`content.config.ts`, route `[...slug]`, hub, layout Article+BreadcrumbList+FAQPage, `PagesLiees`, CTA, capture produit, sitemap) + **y câbler le beacon Web Analytics** (cf [[project_cloudflare_web_analytics]]).
3. Décider le préfixe d'URL : `/methodologie/` (recommandé, aligné sur la page existante) vs `/guides/` vs `/ressources/`.
