# Reprise session sen-ai-website — 2026-05-15+

## À lire AVANT de répondre

1. `~/.claude/projects/C--Users-leed-sen-ai-website/memory/MEMORY.md` (auto-loaded)
2. `~/.claude/projects/C--Users-leed-sen-ai-website/memory/project_todo_tracker.md` — section "État actuel" en haut
3. **Si on attaque Phase D :** `~/.claude/plans/sen-ai-phase-d-sitemap-index.md` (~360 lignes, plan complet locked) + `~/.claude/plans/sen-ai-phase-d-kickoff-prompt.md` (prompt à coller pour Day 1)
4. `project_trust_sources_architecture.md` — rappel denylist HARD + prefer-hint SOFT
5. `feedback_no_hardcoded_vertical.md` — règle SaaS multi-vertical
6. `feedback_cap_user_triggered_llm_ops.md` — règle endpoint user-triggered LLM
7. `project_vps_legacy_shadow_pitfall.md` — piège VPS COPY après refactor (incident Stripe 14 mai)
8. `project_roadmap_content_port.md` — vision 7 piliers (Pilier 3 ciblé Phase D, Pilier 6 Phase G)

## Bilan session 2026-05-14 (4 commits + 1 hotfix prod + 2 plans rédigés ✅)

### Commits (poussés origin/master)

| # | SHA | Theme |
|---|---|---|
| 1 | `1277532` | **Settings UI trust-sources** : page Astro + POST/DELETE /extra-domains + lien index |
| 2 | `145e718` | Cleanup tooltip — retire mention coût OpenAI dans Settings trust-sources |
| 3 | `a14af84` | **LLM auto-suggest LEAD à la matérialisation** : `services/lead_picker.py` (1 batched Claude Haiku call par scan), wired into materialize Phase 1.5, content_metadata.lead_suggestion persistence, UI chip "Auto" sur LEAD row qui disparaît au premier override star toggle |
| 4 | `8f1c506` | **fix(content) Regenerate honors per-item LEAD override** — `resolve_promotion` ne lisait que scan + workspace, ignorait `item.promoted_brand_ids`. Bug latent depuis `190d9d8` (12 mai), révélé par auto-suggest qui pose désormais l'override à la matérialisation |

### Hotfix prod non-versionné

Supprimé 5 fichiers legacy `/root/sen-ai-website/api/*.py` (auth/brands/clients/scans/stripe) qui shadowaient le SDK PyPI Stripe → checkout `Customer.create` cassé depuis fin avril sans qu'on s'en rende compte. Détail dans `memory/project_vps_legacy_shadow_pitfall.md`.

### Plans rédigés (pour la prochaine session)

- `~/.claude/plans/sen-ai-phase-d-sitemap-index.md` — Phase D complet, 7-8 jours, 4 ajouts expert baked in (body content 300 mots dans embedding, authority signal via internal_inlink_count, gamme path bias, top-3 picker UX au lieu d'opaque % confidence chip)
- `~/.claude/plans/sen-ai-phase-d-kickoff-prompt.md` — prompt prêt à coller dans une nouvelle session pour démarrer Day 1

## État branche

`master` à `8f1c506`, pushé sur `origin/master`. Working tree clean (sauf submodule `worker/seo_llm` untracked = normal).

## Prod state

- 5 containers up (postgres / api / astro / worker / nginx) après deploy 14 mai 14:00
- Worker : 16 handlers (discover_trust_sources + lead_picker integrated dans materialize_content_items)
- Migrations appliquées : 020-024
- `services/lead_picker.py` (Claude Haiku) live, 1 batched call par scan à la matérialisation, ~$0.01/scan
- API endpoints `/trust-sources/extra-domains` (POST/DELETE) + `/trust-sources/discover` live
- UI `/app/settings/trust-sources` live + `/app/content/[id]` affiche chip Auto LEAD

## Reste à faire — par priorité

| Item | Effort | Priorité | Source |
|---|---|---|---|
| **Phase D — Sitemap index** (Pilier 3 + débloque Pilier 4 Voice) | 7-8j | 🟢 next big | Plan locked dans `~/.claude/plans/sen-ai-phase-d-sitemap-index.md` |
| **Phase G — Publish frictionless (WordPress / Webflow)** (Pilier 6) | 5-7j | 🟢 reco expert (10× plus ROI user que Phase D) | Roadmap content port |
| Élargir `_SCIENTIFIC_ALLOWLIST` generate_faq.py → quality_score ≥80 | ~30min | 🟡 cheap | Suite session 12 mai |
| MEDIUM-risk LLM endpoints : auto-classify topics / scans/retry / fetch-keywords | ~1h chacun | 🟡 audit subagent | Memoire |
| slowapi key migration IP → user.id | ~30min | 🟡 | Backlog |
| UI aliases sur ClientBrand (SQL-only aujourd'hui) | ~1h | 🟢 (Pilier 1) | Backlog |
| Phase E élargie — Side-by-side validation + measurement loop | ~14j | 🟢 long-term | Pilier 5 + 7 |
| Phase F — Voice fingerprint | ~7j | 🟢 long-term | Pilier 4, dep Phase D |
| Phase C extended — GEO article generator | ~12-15j | 🟢 long-term | Pilier 1 dans articles |
| APScheduler infra | ~0.5j | 🟢 | dep pour cron Phase D v2 + measurement loop |

## ⚠️ Arbitrage stratégique posé (post-expert review)

Après revue du plan Phase D sous lentille marketing/AEO, **Phase G (publish frictionless) débloquerait 10× plus de ROI utilisateur que les marginal gains de Phase D** : le marketer copy-paste manuellement HTML → CMS → schema, c'est sa friction #1. Phase D améliore le target_url matching, Phase G ferme la boucle scan→publish→measure pour un démo killer.

Reco posée dans le plan Phase D (strategic addendum) : enchaîner Phase D puis Phase G. Mais option B existe : pivoter directement sur Phase G maintenant (Phase D minimal en 3-4j ou différé). À arbitrer en début de prochaine session selon appétit produit.

## ⚠️ Pièges à connaître (rappels)

### Infra
- `api/.env` peut perdre des vars silencieusement → diff `api/.env` vs `api/.env.save`
- Après `docker compose up -d` qui recrée un container → TOUJOURS `docker compose restart nginx`
- **VPS legacy file shadow** (memory `project_vps_legacy_shadow_pitfall.md`) : `COPY . .` dans Dockerfile peut embarquer des orphans qui shadowent des packages PyPI. Après tout refactor qui DELETE des fichiers `/api/*.py` ou `/worker/*.py`, mirror le delete sur VPS : `ssh root@... "rm /root/sen-ai-website/api/<file>.py"` avant rebuild
- Submodule `worker/seo_llm` = vertical-locked Pierre Fabre. JAMAIS éditer dedans, toujours wrapper côté SaaS

### LEAD picker (commit a14af84) + Regenerate fix (commit 8f1c506)
- `services/lead_picker.py` skip quand client a 0-1 brands avec domain (no choice)
- `content_metadata.lead_suggestion = {brand_id, reason, source: 'auto'|'user', model}` — source flip de 'auto' à 'user' au premier PATCH qui change promoted_brand_ids
- UI chip "Auto" via `isAutoLead()` + `autoLeadTooltip()` dans `/app/content/[id].astro`
- **Regenerate FAQ** : `resolve_promotion()` ignore item.promoted_brand_ids — generate_faq.py REORDER la promotion list après resolve pour que l'override item gagne (sinon Regen revient à Avène alors que user a picked Aderma)

### Trust sources (commit bb2a2c9, valid 12 mai)
- **HARD denylist** (compliance) : `services/competitor_domains.py` + `services/url_filter.py` (post-filter sur web_search outputs)
- **SOFT prefer-hint** (quality) : `services/trust_sources.py` injecté dans question_text avant super()
- JAMAIS d'allowlist hardcodée. Universal layer = Wikipedia + .gov / .gouv.fr / .europa.eu / .int pattern-matched

### Billing & caps
- content_credit debit au POST `/api/content-items/{id}/generate` (1 credit / FAQ), 402 si insufficient, refund net-aware on permanent failure
- 3 endpoints free-LLM cap 5/item : generate-brief (scan), brief/generate (client), generate-questions (persona)
- Rematch FAQ URL cap 10/item
- lead_picker : system-triggered, ~$0.01/scan, bounded par scan-end, pas user-triggered direct → conforme `feedback_cap_user_triggered_llm_ops.md`
