# Plan d'implémentation - BYOK outils SEO (YourTextGuru + Babbar)

*Rédigé 2026-07-22 avant dev, ancré sur investigation code (4 agents). Objectif : laisser chaque org apporter sa propre clé YourTextGuru / Babbar au lieu de partager la clé plateforme unique. Motivation : (1) rate-limit / contention multi-tenant, (2) COGS - YTG = 84 % du fixe (498,56 €/mo), le sortir vers les clés clients règle l'unique vrai trou de marge de l'audit. Voir [[project_pricing_audit_2026_07_22]] et [[project_byok]].*

> **STATUT (2026-07-22) : Phase 1 IMPLÉMENTÉE en working tree, NON commitée, NON déployée** (branche master). 6 fichiers + migration 065 (détail §4 Lot 1+2). Vérif statique OK (py_compile + appelants). Smoke runtime en attente de déploiement (migration + rebuild api/worker + vraie clé YTG). Phase 2 (remise article 3→2 crédits + UI) à faire. UI absente en Phase 1 → clé posable uniquement via API PUT tant que Phase 2 pas livrée.

---

## 1. Principe produit

- **Défaut = clé plateforme.** Aucune friction pour le self-serve. Un client lambda n'a pas de compte YTG/Babbar.
- **BYOK-SEO = opt-in « escape hatch ».** Positionné **perf/scaling** (« tu satures / c'est lent → passe ta clé »), pas « aide-nous à réduire nos coûts ». Le client s'en fiche de notre COGS ; il achète du débit et de l'isolation.
- **Priorité YourTextGuru** (84 % du fixe). Babbar en compagnon (même compte babbar.tech, isolation rate-limit). HaloScan en extension optionnelle. **LinkFinder hors périmètre** (voir §2).
- **Le levier business** : quand un gros consommateur de contenu apporte sa clé YTG, notre COGS article tombe de ~17 € (@30/mo) à ~0,06 €. La ligne fixe 499 €/mo cesse de croître avec le volume et se réduit à un petit plan « trial/casual ».

## 2. Verdicts de faisabilité (investigation code)

| Outil | Verdict | Raison technique |
|---|---|---|
| **YourTextGuru** | **EASY** | 1 seule instanciation (`geo_content_generator.py:1534 self.ytg = YTGClient()`). Seul chemin prod = `generate_article.execute()` qui a **déjà** `client_id` en scope ET **fait déjà tourner une fenêtre `patched_llm_env`** (`generate_article.py:1230-1246`). YTG lit `os.getenv("YOURTEXTGURU_API_KEY")` au constructeur → **env-patchable**, le constructeur accepte déjà un param `api_key`. **Zéro plomberie, zéro edit du sous-module seo_llm.** |
| **Babbar** | **EASY (per-scan) / N/A (sweep)** | Env-patchable (`BABBAR_API_KEY`). Contexte org dispo sur 2 sites : génération article (`geo_content_generator.py:1535`) + audit concurrent (`audit_competitor_pages.py:367`, `execute(scan_id)`). Le gros consommateur `enrich_with_babbar` (`media_catalog_io.py:541`) est un **sweep cross-client volontairement sans contexte** → reste plateforme (par design, comme discover_media_catalog). |
| **HaloScan** | **MEDIUM** | Contexte org dispo aux 2 sites (`fetch_keywords`, `detect_competitors`, tous `execute(scan_id)`) MAIS lit le **singleton pydantic figé `settings.haloscan_api_key`** (pas `os.getenv`) → l'astuce env-patch NE marche PAS. Il faut **threader une clé/client_id** dans 2 fonctions publiques + `_request` (peu profond mais changement de signature). Côté SCAN, pas contenu → autre bucket de coût (62 €/mo seulement). |
| **LinkFinder** | **HORS PÉRIMÈTRE** | **Ce n'est pas une clé API** - c'est une session de scraping login (cookie OU email+password sur link-finder.net). Per-org = stocker le login marketplace de chaque org (ask sécurité/produit lourd). Data = prix marketplace **partagés** écrits dans le catalogue global → per-org n'apporte quasi rien. 12,50 €/mo. **Skip.** |

**Fait décisif** : le mécanisme BYOK par-org existe déjà et tourne déjà **dans la fonction exacte qui pilote YTG**. YTG est donc un branchement, pas un chantier.

## 3. Décisions d'architecture

1. **Nouveaux providers** : `yourtextguru`, `babbar` (et `haloscan` réservé pour plus tard). Stockés dans `OrganizationApiKey` (générique, `provider TEXT`).
2. **Migration OBLIGATOIRE** : la colonne `provider` a une CHECK constraint `ck_org_api_keys_provider CHECK (provider IN ('openai','anthropic','gemini','mistral'))` + UNIQUE `(organization_id, provider)` (vérifié `060_byok_organization_api_keys.sql:24-25`). Un INSERT `provider='yourtextguru'` échoue aujourd'hui au niveau DB. → nouvelle migration `ALTER ... DROP CONSTRAINT ck_org_api_keys_provider, ADD CONSTRAINT ... CHECK (provider IN (...+ 'yourtextguru','babbar','haloscan'))`.
3. **Ne PAS élargir `BYOK_RUNTIME_PROVIDERS`** (`byok_preflight.py:36`, `byok.py:53`). Cette tuple pilote la remise **scan** -50 % + le gate model-override. Ajouter des outils SEO dedans casserait le pricing scan. → **tuple SÉPARÉE** `BYOK_SEO_PROVIDERS`.
4. **Remise = crédits CONTENU uniquement** (les crédits scan ont déjà la remise LLM-BYOK). Gate sur **YTG actif** (le driver de coût à 84 % ; l'article utilise toujours YTG). Babbar = compagnon optionnel pour l'isolation, pas le gate.
5. **Injection runtime = env-patch** pour YTG + Babbar (réutilise `patched_llm_env`, étendu). Aucun edit du sous-module `seo_llm`. HaloScan (plus tard) = threading explicite car singleton `settings`.
6. **Pas de cap $ pour le SEO en v1.** Le cap actuel = `SUM(llm_usage_log.cost_usd) WHERE key_source='byok'` (LLM-shaped, pas de $/appel pour YTG). Le compte YTG du client EST la limite naturelle. Cap au nombre d'appels = évolution éventuelle, pas v1.
7. **Validation clé** : YTG a `get_status()` (GET /status « tokens restants », `ytg_client.py:126`, actuellement jamais appelé) = **ping de validation parfait** (valide la clé ET renvoie le solde). Babbar = un appel authentifié bon marché (`api_token`). → brancher un validateur par outil dans `_ping_request`.

## 4. Découpage en lots (PR par PR)

### Lot 0 - Décision pricing (toi, avant dev)
- **Taux de remise crédits contenu** quand l'org apporte YTG (voir §7). Reco : **-50 %** (symétrie avec le scan, une seule règle mentale) OU **-30 %** (conserve plus de vélocité-revenu). Dans les deux cas accrétif (COGS→~0).
- **Gate** : YTG seul, ou YTG+Babbar requis. Reco : **YTG seul**.
- **Bonus one-time** contenu à l'activation (mirror des +200 cr scan) ? Reco : optionnel, ex. +30 crédits contenu.

### Lot 1 - Stockage + saisie de clé (backend, effort S)
- **Migration** (nouvelle, ex. `06X_byok_seo_providers.sql`) : ALTER `ck_org_api_keys_provider` pour ajouter `yourtextguru`, `babbar`, `haloscan`.
- **Parité modèle** : mettre à jour les docstrings `provider` dans `api/models.py:96` ET `worker/models.py` (pas de changement de type, juste le commentaire d'allowlist).
- **`api/services/llm_key_validator.py`** : ajouter `yourtextguru`/`babbar` à `BYOK_PROVIDERS` (:20) + `_PROVIDER_LABELS` (:22) + branches `_ping_request` (:32-46) : YTG → `get_status`, Babbar → appel authentifié léger. (Module mal nommé « llm » mais fonctionnel ; rename `key_validator` optionnel.)
- **`api/routers/org_api_keys.py`** : rien à changer - l'allowlist importée gate déjà PUT/DELETE/validate/list. Ajouter les providers à la tuple suffit.
- **Résultat** : une org peut stocker + valider une clé YTG/Babbar via les endpoints existants.

### Lot 2 - Injection runtime YTG + Babbar (worker, effort S) ← LE GROS WIN
- **`worker/services/byok.py`** : ajouter `resolve_ytg_key(db, client_id)` / `resolve_babbar_key(db, client_id)` (mirror de `resolve_openai_key`, renvoient `(key, source)` avec fallback plateforme). Étendre `patched_llm_env(...)` avec params `ytg_key` / `babbar_key` qui patchent `YOURTEXTGURU_API_KEY` / `BABBAR_API_KEY`.
- **`worker/handlers/generate_article.py:1230-1246`** : la fenêtre `patched_llm_env` existe déjà - y ajouter les clés YTG/Babbar résolues. Comme YTG/Babbar lisent `os.getenv` au constructeur et que la construction du generator est dans la fenêtre, **ça marche sans autre edit**.
- **`worker/handlers/audit_competitor_pages.py:367`** : envelopper l'usage Babbar dans `patched_llm_env` (ou passer `api_key=`).
- **Erreurs d'auth** : brancher les 401/403 YTG/Babbar sur `mark_org_key_invalid()` (`byok.py:223`) comme le LLM-BYOK, pour flaguer une clé morte au lieu de replanter en boucle.
- **Résultat** : la génération d'article utilise la clé YTG/Babbar de l'org si présente, plateforme sinon (fallback doux). **Isolation rate-limit + bascule COGS obtenues, sans aucun changement de pricing.**

### Lot 3 - Remise crédits contenu (pricing, effort M)
- **`api/services/byok_preflight.py`** : ajouter `BYOK_SEO_PROVIDERS = ("yourtextguru",)` (gate YTG) + `org_has_seo_byok(db, org_id) -> bool` construit sur `_org_keys()` existant. NE PAS toucher `BYOK_RUNTIME_PROVIDERS`.
- **`api/routers/content_items.py`** : entre le calcul `credit_cost` (:1097) et le débit `add_credits` (:1132), appliquer la remise EN CRÉDITS ENTIERS (pas un %) : `if org_has_seo_byok(_org_id_for_client(db, str(item.scan.client_id))) and item.content_type == "netlinking_article": credit_cost = 2` (au lieu de 3). FAQ inchangée. Le 402 (:1141) référence `credit_cost` → reste correct automatiquement.
- **⚠️ FOOT-GUN REFUND (critique)** : le refund contenu (`worker/main.py:255-267`) matche le débit par **description EXACTE** `f"{content_label} generation: {item_id}"`. **NE PAS** suffixer « - 50% BYOK » à la description contenu (contrairement au scan) sinon `.in_(debit_descs)` ne matche plus → refund 0. **Garder la description byte-identique** ; tracer la remise ailleurs (booléen sur le content_item / audit_log). Les refunds sont net-from-ledger → remboursent automatiquement le montant remisé.
- **Exclure** la charge suggest-media web-search (`content_items.py:1307/1406`) : c'est un coût OpenAI web_search, pas YTG.
- **(Optionnel)** endpoint/champ cost-estimate contenu pour prévisualiser le prix remisé (il n'existe pas aujourd'hui, contrairement au scan `/cost-estimate`). Défférable.
- **Résultat** : crédits contenu remisés pour les orgs content-BYOK.

### Lot 4 - UI (effort M)
- **`src/pages/app/settings/api-keys.astro`** : ajouter YTG/Babbar à `PROVIDER_META` (:47-73, la carte est une boucle `.map` → ajout d'objet). Ajouter au `names` map (:264). Étendre `chipLabel()`/`chipCls()` (:279-295) - réutiliser le précédent `mistral` (« stored / active »).
- **Recadrage « LLM API keys »** : YTG/Babbar ne sont pas des LLM. → soit une **2e section** « Outils SEO », soit renommer la page « API keys ». Ajuster titre (:99), bloc « How it works » (:136-148), carte settings index (`settings/index.astro:230`).
- **Messaging remise** : « apporte ta clé YTG → -X % sur les crédits contenu » (analogue à `api-keys.astro:144` et la FAQ FR `pricing.astro:242-246`). i18n : app EN hardcodé, pricing public FR hardcodé. Tooltips → `src/components/Tip.astro:~326`.
- **Combler le gap `bonus_granted`** si bonus contenu (le frontend ignore aujourd'hui `bonus_granted` de la réponse PUT).
- **Résultat** : saisie self-serve + messaging incitatif.

### Lot 5 - HaloScan (optionnel, plus tard, effort M)
- Threader clé/client_id dans `fetch_domain_positions` / `fetch_site_competitors` / `_request` (`haloscan_client.py`), car singleton `settings` non env-patchable. Côté scan (62 €/mo). Faible priorité vs YTG.

## 5. Foot-guns & risques

1. **Refund contenu par description exacte** (Lot 3) - garder la description identique. LE piège n°1.
2. **Ne pas élargir `BYOK_RUNTIME_PROVIDERS`** - casserait le pricing/blocage scan.
3. **Parité modèle** api/worker (`models.py` x2) + migration - règle « PARITÉ obligatoire ».
4. **Pas de pré-check de solde YTG** (`get_status` jamais appelé aujourd'hui) : si la clé de l'org est à sec en plein article → 429/échec job. Brancher sur `mark_org_key_invalid` + laisser le retry gérer. Quota per-org non enforced en v1 (par design : c'est le compte du client).
5. **Cache disque Babbar 7 j partagé par domaine** (`babbar_client.py:35`) : un cache-hit ne consomme pas la clé de l'org (faits domaine, OK), mais noter que le cache est global cross-org.
6. **Résiduel plateforme même en content-BYOK** : le grounding Gemini + le fan-out Haiku (non loggé) restent sur nos clés (quelques centimes/article). content-BYOK ≈ ~0 plateforme, pas exactement 0.
7. **`patched_llm_env` thread-safety** : sûr car worker = boucle single-thread séquentielle (inchangé).
8. **Validation YTG/Babbar** = vrai appel authentifié (pas de « models-list » gratuit) → assumer un léger coût par validation (cap user-triggered ok, c'est 1 appel).

## 6. Effort & séquençage recommandé

| Phase | Lots | Effort | Ce que ça livre |
|---|---|---|---|
| **Phase 1 - MVP « escape hatch »** | Lot 1 + Lot 2 | ~1-1,5 j | Une org apporte sa clé YTG(+Babbar), la génération d'article l'utilise. **Résout la contention rate-limit + bascule le COGS**, SANS changement de pricing. Déployable et testable seul (cas « tu satures → passe ta clé »). |
| **Phase 2 - La carotte** | Lot 3 + Lot 4 | ~2 j | Remise crédits contenu + UI/messaging. L'incitation client à réellement passer sa clé. |
| **Phase 3 - Extension (option)** | Lot 5 (HaloScan) | ~1 j | Isolation scan-side. Faible ROI (62 €/mo). |

**Total YTG+Babbar content-BYOK (Phases 1+2) : ~3-3,5 j.** Déployer entre les deux ([[feedback_deploy_between_risky_changes]]).

**Pourquoi ce split** : la Phase 1 sert déjà le cas d'usage que tu as décrit (« ça bloque/trop lent → conseil de porter sa clé ») sans toucher au pricing. La Phase 2 ajoute l'incitation économique. On peut s'arrêter après la Phase 1 si l'objectif immédiat est juste l'isolation rate-limit.

## 7. Décision pricing (Lot 0) - TRANCHÉE 2026-07-22

| Paramètre | **Décision** | Raison |
|---|---|---|
| **Remise crédits contenu** | **Article : 3 → 2 crédits** en BYOK-YTG (FAQ inchangée à 1). **Jamais exprimé en « % »** : le produit affiche le compte de crédits réel (cost-estimate). | Un « -50 % » ne tombe pas juste sur 3 crédits (ceil(3/2)=2 = -33 %, pas -50 %) ; « 2 crédits au lieu de 3 » est un entier propre et honnête. La FAQ n'utilise pas YTG → reste 1 (pas de règle spéciale). Cohérent + simple vs la complexité d'une FAQ discountée. |
| **Gate de la remise** | **YTG actif seul** | Driver de coût à 84 % ; l'article utilise toujours YTG. Babbar = compagnon isolation, pas gate. |
| **Bonus activation** | **Aucun en v1** | Garder la Phase 2 lean ; lever activable plus tard. |
| **Périmètre v1** | **YTG + Babbar** | HaloScan Phase 3, LinkFinder abandonné. |

**Ordre d'exécution : Phase 1 d'abord** (Lot 1 + Lot 2, IMPLÉMENTÉE), déployée et validée, PUIS Phase 2 (remise article 3→2 cr + UI complète). Note : le scan garde son « -50 % » existant (vrai sur des centaines de crédits) ; seul le contenu passe en compte-de-crédits concret.
