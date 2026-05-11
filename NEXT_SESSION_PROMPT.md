# Reprise session sen-ai-website — 2026-05-12+

## À lire AVANT de répondre

1. `~/.claude/projects/C--Users-leed-sen-ai-website/memory/MEMORY.md` (auto-loaded)
2. `project_todo_tracker.md` — section **"État actuel"** en haut du fichier
3. `feedback_no_hardcoded_vertical.md` — règle clé : sen-ai est SaaS multi-vertical, ZÉRO hardcoded brand/competitor/vertical-specific dans code shared
4. `project_roadmap_content_port.md` — vision long-terme 7 piliers UX

## Bilan session 2026-05-11 (13 commits — record absolu)

**Le brand bias FAQ scan compétiteur = résolu end-to-end + billing wired + UX clarity.**

### Commits dans l'ordre

| # | SHA | Theme |
|---|---|---|
| 1 | `73cd7ef` | docs roadmap 7 piliers UX |
| 2 | `58d964c` | A2 bridge ScanOpportunity → ScanContentItem + manual target_url pick UI |
| 3 | `c3cc4b0` | fix typo ScanQuestion.question |
| 4 | `bbbd4d7` | Auto-suggest target_url via FAQPageMatcher (seo-llm submodule) + URL validation client-side |
| 5 | `cbb2a3d` | Dedup primary_brand_domains + PF cleanup migration 021 + inline domain edit |
| 6 | `fd32565` | **Root cause** : drop SBC merge dans BrandResolver + classify_topics flip site_brand→competitor sur audit |
| 7 | `3da3b4c` | 3 quick wins : generic utm strip + content_credit debit + workspace_brief wire dans 3 analysers |
| 8 | `46a1bcd` | docs NEXT_SESSION_PROMPT refresh |
| 9 | `cd80551` | Refund content_credit on FAQ failure (net-aware, scoped, scan reste 'completed') |
| 10 | `d52be6f` | Wizard scan_type persist (own_brand / competitor_audit) — authoritative pour is_competitor_scan |
| 11 | `be988f7` | Target URL collision detection + warning chip "🔗 Also picked by N other item(s)" |

### Smoke tests prod validés
- Scan uriage.fr `eae8d1fd` → 7 ContentItems auto-suggérés sur eau-thermale-avene.fr (0 Uriage)
- FAQ generated → quality 91/100, "La Crème Relipidante XERACALM AD d'Eau Thermale Avène"
- `promoted_brand_ids` audit = 6 PF user brands (Avène lead → Pierre Fabre Oral Care). 0 compétiteur.
- Refund cycle (debit -1 → fail → refund +1) × 3 → net = 0, scan reste `completed`, item retry-ready
- Wizard `scan_type` envoyé au POST, 24 PF scans backfillés (5 own / 19 competitor)
- 4 items dans collision sur xeracalm-ad page → chip ambre visible dans validation page

## État branche

`master` à `be988f7`, working tree propre (sauf untracked content dans submodule `worker/seo_llm` = normal).

## Prod state

✅ 5 containers up (postgres / api / astro / worker / nginx)
✅ Worker boot clean avec 14 handlers (`materialize_content_items` ajouté Phase B)
✅ Migrations appliquées : 020 (target_url_source), 021 (PF brand cleanup), 022 (scan_type)
✅ Toutes features déployées + smoke validé

## Reste à faire — par priorité

| Item | Effort | Priorité | Pilier |
|---|---|---|---|
| Wire `format_workspace_brief` dans `run_llm_tests` + `cleanup_brands` (restant 2 analysers) | ~20min | 🟡 | 1 |
| FAQ-specific validation UI (Q/R rows view) | ~1.5h | 🟡 polish | 5 (foundation) |
| Bouton "Find a different page" dans validation page (re-run matcher avec exclusion list) | ~1.5h | 🟡 quality | 3 |
| Bouton "Merge these N items into one richer FAQ" dans validation page | ~3h | 🟡 quality | 5 |
| Décider site.json (PF testimonial vs neutre) | discussion | 🟡 marketing | — |
| Tip.astro tooltips vertical-neutres | ~20min | 🟢 polish | — |
| **Phase D** — Sitemap index (Pilier 3 full + débloque Pilier 4 voice fingerprint) | ~5j | 🟢 next big | 3, 4 |
| **Phase E** élargie — Side-by-side validation + measurement loop | ~14j | 🟢 long-term | 5, 7 |
| **Phase F** — Voice fingerprint | ~7j | 🟢 long-term | 4 |
| **Phase G** — CMS integrations (WP / Webflow / Shopify) | ~10j | 🟢 long-term | 6 |
| APScheduler infra (Phase D + Phase E dep) | ~0.5j | 🟢 | — |
| Site type classifier `classify_citation_domains.py` | ~1j | 🟢 | — |

## ⚠️ Pièges à connaître

### Infra
- `api/.env` peut perdre des vars silencieusement → toujours `diff api/.env api/.env.save` avant d'assumer qu'une clé est set
- Après `docker compose up -d api` qui recrée le container : TOUJOURS `docker compose restart nginx` sinon 502 (nginx cache l'ancienne IP)
- Submodule `worker/seo_llm` = vertical-locked seo_llm CLI Pierre Fabre. JAMAIS éditer dedans. Toujours wrapper côté SaaS avec stub / subclass pour découpler.

### Logique brand bias (verrouillée 2026-05-11)
- **BrandResolver ne lit PLUS SBC** : promote chain = `scan.promotion_brand_ids OR client.primary_brand_ids OR raise`. SBC reste stocké pour analytics mais ne décide plus de la promote chain.
- **classify_topics flip** : sur scan compétiteur, `site_brand/site_gamme` → SBC `competitor` au lieu de `my_brand`. Détection via `is_competitor_scan()` qui priorise `scan.scan_type` (autoritaire) puis fallback `client.primary_brand_ids` heuristic.
- **`scan.scan_type`** = source de vérité pour competitor-vs-own. Set par le wizard, validé en API. Si NULL (anciens scans hors backfill), heuristic prend la main.
- **Onboarding workflow** : user DOIT setter `client.primary_brand_ids` AVANT premier scan pour que le heuristic fonctionne bien. UI `/app/settings/brands` permet ça self-service avec inline domain edit.

### Billing
- **Content credit debit** au POST `/api/content-items/{id}/generate` (1 credit par FAQ). 402 si insufficient.
- **Refund on permanent failure** : helper net-aware `_refund_content_item_credit()` dans worker/main.py. Le scan parent reste `completed`, l'item reset `identified` pour retry. Idempotent par delta.
- **`_refund_scan_credits` n'est pas utilisé sur content-item jobs** (over-refund). Le set `CONTENT_ITEM_JOB_TYPES = {"generate_faq"}` route vers le path scoped. Ajouter `generate_article` quand Phase C ship.

### Migrations PF-specific
- `021_pf_brand_cleanup.sql` = one-off PF data (190 → 6 brands). Autres clients gèrent leur cleanup self-service via `/app/settings/brands`.
- Backfill scans `scan_type` PF fait en SQL session, pas dans migration 022.

### Tracking params
- URLs des matchers (FAQPageMatcher, web_search) peuvent contenir `?utm_source=...` ou autres trackers. Le `_strip_tracking_params()` dans `materialize_content_items.py` les vire (utm_*, mc_*, ga_*, gclid, fbclid, msclkid, yclid, wbraid, gbraid, ref, src, _hsenc, _hsmi). Generic, marche pour tous providers.
- Si on découvre un nouveau tracker provider, ajouter au `_TRACKING_PARAM_PREFIXES` ou `_TRACKING_PARAM_EXACT`.
