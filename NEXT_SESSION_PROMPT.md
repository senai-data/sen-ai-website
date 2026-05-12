# Reprise session sen-ai-website — 2026-05-14+

## À lire AVANT de répondre

1. `~/.claude/projects/C--Users-leed-sen-ai-website/memory/MEMORY.md` (auto-loaded)
2. `project_todo_tracker.md` — section **"État actuel" 2026-05-13** en haut (7 commits + deploy + UI surfacing validé)
3. `project_trust_sources_architecture.md` — règle 2026-05-13 finale : **denylist HARD (competitor) + prefer-hint SOFT (trust sources)**, surtout pas allowlist
4. `feedback_no_hardcoded_vertical.md` — règle SaaS multi-vertical
5. `feedback_cap_user_triggered_llm_ops.md` — règle endpoint user-triggered LLM
6. `project_roadmap_content_port.md` — vision 7 piliers

## Bilan session 2026-05-13 (5 commits — pushés + déployés + validés end-to-end ✅)

**Session "content quality architecture pivot"** : initial allowlist refactor (918451b), smoke test mesuré le gap, pivot vers denylist + prefer-hint (bb2a2c9), redéploy, smoke test prouvant **quality 72→91/100 (+19)** + brand-bias clean.

### Commits dans l'ordre

| # | SHA | Theme |
|---|---|---|
| 1 | `c7b008e` | stopgap +12 cross-vertical refs (transitionnel, deprecated) |
| 2 | `918451b` | trust sources discovery initial (allowlist filter — déprécié) |
| 3 | `6e7389c` | docs refresh |
| 4 | `f5127e2` | prompt v2 discovery — sector balance |
| 5 | `bb2a2c9` | **refactor denylist + prefer-hint (le bon design, validé)** |

### Architecture finale (`bb2a2c9`) — two-layer brand-bias defense

**HARD denylist** (compliance) :
- `services/competitor_domains.py::get_competitor_domains_for_scan(scan_id, db)` — DB-backed, déterministe
- `services/url_filter.py::partition_urls(urls, competitor_domains)` — competitor + universal e-commerce/social/blog patterns + diagnostic logging
- Post-filter sur web_search outputs

**SOFT prefer-hint** (quality) :
- `services/trust_sources.py::get_trust_sources_for_client(client_id, db)` — per-client discovered + UNIVERSAL_REFERENCES + extras
- Injecté dans le `question_text` AVANT super() : *"PRIORITISE authoritative sources from these domains: …"*
- URLs hors trust list passent quand même — recall préservé

### Smoke test prod validé end-to-end

Item `e0915e0d` (FAQ atopie pédiatrique, scan compétiteur uriage.fr, client PF) :
- quality 72 → **91/100** ✅
- sources 1 → **3** (Avène + 2× dermato-info.fr) ✅
- mentions Uriage/Xémose/LRP : **0** ✅
- denylist drops : 0 (prefer-hint a suffi)

### Prod state (post-deploy final 21:57 le 2026-05-12)

- 5 containers up : postgres, api, astro, worker, nginx
- Worker 16 handlers (discover_trust_sources ajouté)
- Migration 024 appliquée (`scan_content_items.content_metadata JSONB`)
- API endpoints `/clients/{id}/trust-sources` (GET + POST/discover) live
- PF client trust_sources discovered : 6 domains (HAS + Ministère + SFD + dermato-info + DermNetNZ + PubMed)
- UI validation page affiche Quality badge + Sources panel avec brand-bias transparency (commit `d0997ff`)

### Commit `d0997ff` — content_metadata + UI

- Schema : `scan_content_items.content_metadata` JSONB (quality_score + sources enrichies + competitor_drops + drop_reasons + scientific_kept/raw + duration + generator_version)
- Worker écrit le payload sur item.content_metadata après chaque regen
- API serializer expose le champ
- UI : badge Quality colour-coded + section Sources avec banner diagnostic brand-bias + cards par source (org name + type chip + lien)
- Smoke test final ✅ : 91/100, 3 sources (Avène + 2× SFD), 0 competitor drops, UI affiché correctement par user

## Bilan session 2026-05-12 (7 commits — pushés origin/master)

**Session "content UX overhaul"** : refonte validation page + per-item LEAD picker + caps LLM ops + 2 wrapper fixes brand-bias (avec regen end-to-end validé) + fix Source link click contenteditable.

### Commits dans l'ordre

| # | SHA | Theme |
|---|---|---|
| 1 | `84084da` | workspace_brief wiring complet (5 analysers couverts au lieu de 3) |
| 2 | `31a796a` | fix brand-bias `_fetch_brand_context` URL filter (premier round, **partiel**) |
| 3 | `1c55097` | caps free LLM ops (3 endpoints) + gammes mgmt drag-drop settings |
| 4 | `190d9d8` | content UX overhaul : rematch + LEAD picker + Kanban toggle + 12 features |
| 5 | `9859913` | docs refresh NEXT_SESSION_PROMPT (4-commit milestone) |
| 6 | `3930415` | fix brand-bias étendu : `_fetch_scientific_context` filtré par allowlist scientifique (regen test ✅) |
| 7 | `1dd7a2d` | fix visual editor : Source link click via `@mousedown` (Chromium contenteditable consume `@click`) |

### Features livrées (détail)

**Validation page `/app/content/{id}`** (commit `190d9d8`) :
- Header sticky : 3 boutons visibles permanents `[✗ Reject] [🔄 Regenerate] [✓ Approve]` (Fitts + Hick's)
- Per-item LEAD picker : star toggle ★/☆ sur Brand promotion (Gate 3 mirror), reorder optimiste + PATCH `promoted_brand_ids` + banner amber "Click 🔄 Find a different page to refresh URL"
- Find a different page : rematch avec exclusion list, cap 10/item (UI + 429 server), ⓘ tooltip simplifié (pas d'OpenAI internals)
- FAQ rendering hierarchy : Q/R cards Common Region, h3 semibold, lien Source ↗
- Brand mention highlights : emerald (own incl. gammes via SBC+parent_id) + orange (competitors)
- Brand promotion : mention counter per-brand (Goal-Gradient) emerald `Nx` / amber `0 mentions`
- Competitor leak panel : conditionnel sur content existing, ✓ clean / ⚠ leak detected avec per-brand count
- Progress bar Generate FAQ (Doherty Threshold)
- Tooltips ⓘ : Content section (SEO/GEO rules), Brand promotion (override semantics)
- Breadcrumb FAQ + topic-name = `<a>` links scopés Kanban (audit subagent : seul breadcrumb cassé app)
- Link clicks dans contenteditable : `window.open` intercept → Source ouvre en new tab
- Show More toggle retiré (Best competitor + Competitors cited toujours visibles)

**Kanban `/app/content`** (commit `190d9d8`) :
- Toggle "Show rejected" dans filter bar → 5e colonne grise "Rejected" + `include_rejected=true`

**Backend** (commit `190d9d8`) :
- `POST /content-items/{id}/rematch-target-url` + 10/item cap + 429
- `PATCH /content-items/{id}` accepte `validation: ""` (clear sentinel) + `promoted_brand_ids: list[str]` (per-item override)
- `_serialize_item` expose `own_brand_names` + `competitor_brand_names` + `all_known_brand_names`
- `_resolve_target_site(scan, db, item=None)` item-aware (préfère per-item LEAD)
- Migration 023 `rejected_target_urls JSONB DEFAULT []`

**Workspace settings `/app/settings/brands`** (commit `1c55097`) :
- Gammes mgmt drag-drop pattern Gate 3 : children indented `↳` sous parent, drag entre My Brands / Available, PATCH `parent_id` real-time
- `GET /clients/{id}/promotion` nested children
- `PATCH /clients/{id}/brands/{brand_id}/parent` body `{parent_id: uuid|null}` enforce 1-level

**Caps free LLM ops** (commit `1c55097`) sur 3 endpoints précédemment unguarded :
- `POST /scans/{id}/generate-brief` cap 5
- `POST /clients/{id}/brief/generate` cap 5 + `@limiter.limit("5/min")` ajouté
- `POST /scans/{id}/personas/{pid}/generate-questions` cap 5 + dedupe ajouté
- Pattern : counter stocké dans JSONB existant (`scan.config.domain_brief.generations_count`, etc.), incrément on success only dans le worker

**Brand-bias defense in depth** (commits `31a796a` + `3930415`) — **fix end-to-end validé** :
- seo_llm a **DEUX paths web_search** qui leakent : `_fetch_brand_context` ET `_fetch_scientific_context`. Le premier seul ne suffit pas.
- `_fetch_brand_context` filtré par `target_site` (URL filter, mode Serper/OpenAI dual-path).
- `_fetch_scientific_context` filtré par allowlist scientifique générique (HAS, ameli, Vidal, ANSM, PubMed, NIH, Cochrane, Nature, Lancet, NEJM, BMJ, JAMA, Wikipedia, etc.). Helper `_domain_in_allowlist` + `_filter_scientific_context_by_allowlist`.
- **Test regen 2026-05-12 19:58 sur item `e0915e0d`** : Xémose 4→0, Uriage 4→0, Avène 5→7, XERACALM 3→5, sources citées 3 Uriage→1 Avène. Texte 100% on-brand (mentions I-MODULIA⁺, LIPID:B2). Quality score 91→72 (penalty seo_llm pour low citation count — trade-off acceptable, élargir allowlist plus tard).

**Workspace_brief 5/5 analysers** (commit `84084da`) :
- `run_llm_tests.py` : BrandAnalyzer domain_context utilise workspace brief
- `cleanup_brands.py` : Claude classifier reçoit workspace brief

**Fix Source link click contenteditable** (commit `1dd7a2d`) :
- `@click` ne suffit pas — Chromium consume le geste pendant `mousedown` pour placer le caret AVANT que `click` ne tire.
- Switch sur `@mousedown="onContentMouseDown"` (+ `@click` en fallback touchscreen) avec helpers `_findAnchorFromEvent` (gère TEXT_NODE → parentElement) + `_isPlainLeftClick` (filtre Ctrl/Meta/Shift/Alt + middle-click pour préserver muscle memory power-user).

## État branche

`master` à `1dd7a2d`, pushé sur `origin/master`. Working tree propre (sauf submodule `worker/seo_llm` untracked = normal).

## Prod state

✅ 5 containers up (postgres / api / astro / worker / nginx)
✅ Worker boot clean avec 15 handlers (`rematch_target_url` ajouté)
✅ Migrations appliquées : 020/021/022/023
✅ Toutes features déployées, syntaxe Python validée, frontend rebuild OK

## Reste à faire — par priorité

| Item | Effort | Priorité | Pilier |
|---|---|---|---|
| **Élargir `_SCIENTIFIC_ALLOWLIST`** (generate_faq.py) pour rapatrier quality_score ≥ 80 | ~30min | 🟡 next | — (suite du fix `3930415`) |
| **LLM auto-suggest LEAD à la matérialisation** (Option C roadmap LEAD picker) | ~3h | 🟡 next | 2 |
| MEDIUM-risk LLM endpoints (audit subagent) : `auto-classify topics`, `scans/retry`, `fetch-keywords` | ~1h chacun | 🟡 | — |
| slowapi key migration IP → user.id | ~30min | 🟡 | — |
| UI aliases sur ClientBrand (currently SQL-only) | ~1h | 🟢 | 1 |
| **Phase D — Sitemap index** (Pilier 3 full + débloque Pilier 4) | ~5j | 🟢 next big | 3, 4 |
| Phase E élargie — Side-by-side validation + measurement loop | ~14j | 🟢 long-term | 5, 7 |
| Phase F — Voice fingerprint | ~7j | 🟢 long-term | 4 |
| Phase G — CMS integrations (WP / Webflow / Shopify) | ~10j | 🟢 long-term | 6 |
| APScheduler infra (dep Phase D + Phase E) | ~0.5j | 🟢 | — |
| Site type classifier `classify_citation_domains.py` | ~1j | 🟢 | — |

## ⚠️ Pièges à connaître

### Infra
- `api/.env` peut perdre des vars silencieusement → toujours `diff api/.env api/.env.save` avant d'assumer qu'une clé est set
- Après `docker compose up -d api` qui recrée le container : TOUJOURS `docker compose restart nginx` sinon 502 (nginx cache l'ancienne IP)
- Submodule `worker/seo_llm` = vertical-locked seo_llm CLI Pierre Fabre. JAMAIS éditer dedans. Toujours wrapper côté SaaS avec stub / subclass pour découpler.

### Logique brand bias (verrouillée 2026-05-12, regen end-to-end validé)
- **BrandResolver ne lit PLUS SBC** : promote chain = `scan.promotion_brand_ids OR client.primary_brand_ids OR raise`. SBC reste stocké pour analytics mais ne décide plus de la promote chain.
- **classify_topics flip** : sur scan compétiteur, `site_brand/site_gamme` → SBC `competitor` au lieu de `my_brand`. Détection via `is_competitor_scan()` qui priorise `scan.scan_type` puis fallback heuristic.
- **`scan.scan_type`** = source de vérité pour competitor-vs-own. Set par le wizard, validé en API.
- **Per-item LEAD override** : `item.promoted_brand_ids[0]` PRIORITAIRE sur workspace LEAD pour le rematch. `_resolve_target_site(scan, db, item=item)` doit toujours recevoir l'item, sinon retombe sur workspace.
- **DEUX paths web_search** dans seo_llm doivent être filtrés (commits `31a796a` + `3930415`) :
  - `_fetch_brand_context` → URL filter par target_site
  - `_fetch_scientific_context` → URL filter par allowlist scientifique (HAS / Vidal / PubMed / NIH / etc.)
  - ⚠ Tout NOUVEAU web_search dans seo_llm DOIT être également filtré ou les compétiteurs ressortent. Pattern : wrapper subclass override + URL filter par domaine cible.

### Billing & caps user-triggered LLM ops
- **content_credit** debit au POST `/api/content-items/{id}/generate` (1 credit par FAQ). 402 si insufficient. Refund net-aware on permanent failure.
- **3 endpoints free-LLM cap 5/item** (commit `1c55097`) :
  - `POST /scans/{id}/generate-brief` → `scan.config.domain_brief.generations_count`
  - `POST /clients/{id}/brief/generate` → `client.apps.client_brief.generations_count`
  - `POST /scans/{id}/personas/{pid}/generate-questions` → `persona.data.questions_generations_count`
- **Rematch FAQ URL cap 10/item** : `item.rejected_target_urls.length >= 10` → 429
- **Règle pour tout nouveau endpoint user-triggered LLM-backed** (voir `feedback_cap_user_triggered_llm_ops.md`) : credit-debited OU hard-capped, jamais free + unbounded.

### UI / Frontend
- **Astro `<style>` scoping** : `<style>` est scopé par défaut. Pour des éléments injectés dynamiquement par Alpine (`x-html`, `renderedContentHtml`), les sélecteurs scopés ne matchent pas → fallback browser yellow `<mark>`. Solution : `<style is:global>` + `!important` au besoin.
- **contenteditable + `<a>`** (commit `1dd7a2d`) : `@click` ne suffit PAS sur Chromium — le navigateur consume le geste pendant `mousedown` pour placer le caret avant que click ne tire. Utiliser `@mousedown` + `preventDefault()` + `window.open(href, '_blank')` (avec `@click` en fallback touchscreen). Edge case TEXT_NODE : remonter à `parentElement` avant `closest('a[href]')`.
- **Star toggle pattern (Gate 3 mirror)** : `★` jaune-400 filled sur LEAD, `☆` gris-300 outline cliquable sur autres. Locked quand status approved/published. Reuse pour toute "pick ONE" UX.
- **Drag-drop hierarchical brands** : pattern Gate 3 (3 cols scan classifier) + workspace settings (2 cols My Brands / Available). Children indented `↳` border-left emerald.
- **Docker layer cache stale** : si un fix Astro ne semble pas prendre, `docker compose build --no-cache astro`. Changes JS inline dans `<script is:inline>` parfois bypass le rebuild incremental.

### Migrations
- `021_pf_brand_cleanup.sql` = one-off PF data (190 → 6 brands). Autres clients gèrent leur cleanup self-service via `/app/settings/brands`.
- `022_scan_type.sql` = scans.scan_type TEXT (own_brand/competitor_audit/NULL). Backfill PF fait en SQL session, pas dans migration.
- `023_rejected_target_urls.sql` = scan_content_items.rejected_target_urls JSONB DEFAULT [].

### Tracking params (commit `3da3b4c`)
- URLs des matchers (FAQPageMatcher, web_search) peuvent contenir `?utm_source=...` etc. Le `_strip_tracking_params()` dans `materialize_content_items.py` les vire (utm_*, mc_*, ga_*, gclid, fbclid, msclkid, yclid, wbraid, gbraid, ref, src, _hsenc, _hsmi). Si nouveau provider, ajouter à `_TRACKING_PARAM_PREFIXES` ou `_EXACT`.

### Patterns à reuse (pour cohérence Jakob's Law)
- **Pick ONE** (focus brand, lead brand) → ★ star toggle (Gate 3)
- **Reorder N items** → ↑↓ buttons hover (workspace settings)
- **Drag-drop classification** → 2-3 columns avec ring highlight (Gate 3 + workspace settings)
- **Run LLM action with cost/cap** → button + counter `X/N tried` + ⓘ tooltip + 429 server (rematch + 3 caps)
- **Audit visualisation** → ranked list numbered + emerald/amber chips + LEAD coral badge (Brand promotion panel)
