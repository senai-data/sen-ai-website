# Reprise session sen-ai-website - post 2026-07-16 soir (P3 + Gemini 3.5 + sélecteur shipped)

## Bilan session 2026-07-16 soir (7 commits, tout déployé + smoke prod par tranche)

- **Act-scope P3 ères de modèles SHIPPED** (`c66d29d` → `0b0be20`) : summary.models
  (+ clé "analyzer") à la complétion, backfill 55/55 scans, trend points
  models+model_changed sur aggregated + workspaces/overview, delta null +
  delta_reason au franchissement, sparkline partagée src/lib/sparkline.ts
  (dots ringés + tooltips old→new), chips "AI models updated"/"AI coverage
  changed" partout où un delta s'affichait (y compris scan-header, miss du
  red-team), methodology EN/FR. Les lignées PF montrent leur vraie ère
  historique au seam import→natif.
- **🔴 Migration Gemini FORCÉE découverte + exécutée** (`26789f7`) :
  gemini-2.5-flash + 2.5-flash-lite shutdown le 16/10/2026, 404 prématurés
  depuis le 9/07. → gemini-3.5-flash (= app Gemini + AI Mode, match
  consommateur) + analyzer gemini-3.1-flash-lite. La frontière P3 s'est
  annotée au point exact = validation en conditions réelles.
- **Sélecteur de version de modèle SHIPPED** (`6c50f96`, `025faf2`, `b1bf9ad`) :
  allowlist parité api/worker (gpt-4.1-mini défaut éco / gpt-5.5 "closest to
  consumer ChatGPT" / gpt-5.6-luna ; gemini-3.5-flash seul ; JAMAIS
  chat-latest), config.model_overrides hérité au rescan, gates 402 (jamais
  400 : tuerait le schedule), override ssi key_source=byok, UI pipeline gated
  BYOK + colonne compliance "Model version (customer-selected)".
  E2e réel gpt-5.5 : **fix critique au passage - GPT-5.x rejette
  `temperature`** (le path openai l'envoyait depuis toujours). Coût réel
  gpt-5.5 : ~$0.43/appel scan. Cleanup harnais vérifié complet.

Détail : mémoires `project_model_selector` (nouvelle) + `project_act_scope_plan`
+ `project_todo_tracker` (section 2026-07-16 soir).

## Prompt à coller pour la prochaine session

```
Reprise sen-ai.fr. Lis project_todo_tracker.md puis project_model_selector
(foot-guns : GPT-5.x/temperature, overlay pricing, 402 vs 400).

Candidats de session (par priorité) :
1. Les 9 alertes Dependabot GitHub (4 high) réapparues au push du 2026-07-16.
2. Act-scope P4 streak opportunities (~2j, cf project_act_scope_plan) -
   status persisting/new + resolved[], cross-ère = feature.
3. Décision pricing : défaut plateforme OpenAI vers un modèle consommateur
   (gpt-5.5 $5/$30 ou gpt-5.6-luna $1/$6 vs gpt-4.1-mini actuel) - P3
   annotera la frontière tout seul, mais crédits à repondérer d'abord.
4. Backlog BYOK : e2e contenu réel, runtime Mistral, agent.py chatbot,
   drop client_api_keys.

Foot-guns : deploy = scp + rebuild + up -d + restart nginx + smoke synchrone ;
diff .env vs .env.save avant deploy (rafraîchis le 16/07 au soir) ; update
tracker après chaque tranche. No em-dash, UI en anglais.
```

## Pièges permanents (rappels)

- api/.env et worker/.env : diff vs .env.save avant deploy ; backup avant édition ;
  éditer le .env LOCAL du repo ne fait RIEN en prod.
- Après `docker compose up -d` qui recrée un container → TOUJOURS `restart nginx`.
- Un `docker compose restart` ne recharge PAS le .env - il faut `up -d` (recréation).
- VPS legacy file shadow : tout DELETE de fichier api/worker doit être mirroré en ssh rm.
- Submodule worker/seo_llm : JAMAIS éditer dedans, toujours wrapper côté SaaS
  (précédents : rotator gemini f3923a8, SAAS_PRICING_OVERLAY).
- Tout bump astro/adapter : tester `node dist/server/entry.mjs` local AVANT deploy.
- Nouveau modèle LLM (allowlist OU défaut plateforme) : prix dans
  SAAS_PRICING_OVERLAY d'abord, sinon caps aveugles ; et tester les params
  (GPT-5.x rejette temperature).
