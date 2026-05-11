# Reprise session sen-ai-website — 2026-05-12+

## À lire AVANT de répondre

1. `~/.claude/projects/C--Users-leed-sen-ai-website/memory/MEMORY.md` (auto-loaded)
2. `project_todo_tracker.md` — section **"État actuel"** en haut du fichier
3. `feedback_no_hardcoded_vertical.md` — règle clé : sen-ai est SaaS multi-vertical, ZÉRO hardcoded brand/competitor/vertical-specific dans code shared
4. `project_roadmap_content_port.md` — vision long-terme 7 piliers UX

## Bilan session 2026-05-11 (8 commits)

**Brand bias FAQ sur scan compétiteur = RÉSOLU end-to-end** :

1. `73cd7ef` — docs roadmap 7 piliers UX
2. `58d964c` — A2 bridge ScanOpportunity → ScanContentItem + UI manual target_url pick
3. `c3cc4b0` — fix typo ScanQuestion.question
4. `bbbd4d7` — Auto-suggest target_url via FAQPageMatcher (seo-llm submodule) + URL validation feedback client-side
5. `cbb2a3d` — Dedup primary_brand_domains + PF workspace cleanup migration 021 + inline domain edit
6. `fd32565` — **Root cause fix** : SBC pollution → BrandResolver drop SBC merge + classify_topics flip site_brand→competitor sur audit competitor

**Smoke test final prod** (scan uriage.fr `eae8d1fd` re-generate) :
- `promoted_brand_ids` = [Avène lead, Aderma, Ducray, Klorane, René Furterer, Pierre Fabre Oral Care]
- **0 Uriage, 0 Xémose, 0 pollution** dans audit trail
- Quality 91/100, 5 Q/R, FAQ explicite "La Crème Relipidante XERACALM AD d'Eau Thermale Avène"

## État branche

`master` à `fd32565`, working tree propre (sauf untracked content dans submodule `worker/seo_llm`).

## Prod state

✅ 5 containers up (postgres / api / astro / worker / nginx)
✅ Worker boot clean avec 14 handlers (ajout `materialize_content_items`)
✅ Migration 020 (`target_url_source` col) + 021 (PF brand cleanup) appliquées
✅ Auto-suggest pipeline E2E validé sur scan compétiteur

## Reste à faire — par priorité

| Item | Effort | Priorité | Pilier |
|---|---|---|---|
| Wire `format_workspace_brief` dans classify_topics + autres analysers | ~30min | 🟡 | 1 |
| Credit debit/refund 1 content_credit/FAQ | ~1h | 🟡 billing | — |
| FAQ-specific validation UI (Q/R rows view) | ~1.5h | 🟡 polish | 5 (foundation) |
| Strip `?utm_source=openai` du target_url dans materialize handler | ~10min | 🟢 cosmetic | — |
| Wizard scan creation : toggle explicite "audit-mode vs own-brand" | ~2h | 🟡 UX clarity | — |
| Décider site.json (PF testimonial vs neutre) | discussion | 🟡 marketing | — |
| Diagnostiquer `_has_faq_section` matcher (renvoie tjrs même page produit) | ~1h | 🟡 quality | 3 |
| Tip.astro tooltips vertical-neutres | ~20min | 🟢 polish | — |
| Phase D — Sitemap index (Pilier 3 full + débloque Pilier 4) | ~5j | 🟢 next big | 3, 4 |
| Phase E élargie — Side-by-side validation + measurement loop | ~14j | 🟢 long-term | 5, 7 |
| Phase F — Voice fingerprint | ~7j | 🟢 long-term | 4 |
| Phase G — CMS integrations | ~10j | 🟢 long-term | 6 |
| APScheduler infra (Phase D + Phase E dep) | ~0.5j | 🟢 | — |
| Site type classifier `classify_citation_domains.py` | ~1j | 🟢 | — |

## ⚠️ Pièges à connaître

- `api/.env` peut perdre des vars silencieusement → toujours `diff api/.env api/.env.save` avant assumer qu'une clé est set
- Après `docker compose up -d api` qui recrée le container : TOUJOURS `docker compose restart nginx` sinon 502 (nginx cache l'ancienne IP)
- Submodule `worker/seo_llm` = vertical-locked seo_llm CLI Pierre Fabre. JAMAIS éditer dedans. Toujours wrapper côté SaaS avec stub / subclass pour découpler.
- **BrandResolver ne lit PLUS SBC** (commit `fd32565`) : promote chain = `scan.promotion_brand_ids OR client.primary_brand_ids OR raise`. SBC reste pour analytics mais ne décide plus de la promote chain.
- **classify_topics flip** : sur scan compétiteur (déduit via `is_competitor_scan`), `site_brand/site_gamme` → SBC `competitor` au lieu de `my_brand`. Si workspace n'a pas `primary_brand_ids` set, fallback legacy (treat as my_brand). Donc onboarding workspace = set primary_brand_ids AVANT premier scan.
- Migration `021_pf_brand_cleanup.sql` = one-off PF-specific. Autres clients gèrent leur cleanup via `/app/settings/brands` (UI self-service avec inline domain edit).
