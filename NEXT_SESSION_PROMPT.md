# Reprise session sen-ai-website - post 2026-07-16 (BYOK shipped)

## Bilan session 2026-07-16 (9 commits, tout pushé origin/master)

- **BYOK beta SHIPPED en 6 tranches** (`516b090` → `3caeadc`) : clés LLM par organisation
  (openai/anthropic/gemini/mistral), Fernet via OAUTH_FERNET_KEY, page
  /app/settings/api-keys, cap $/mois par provider (llm_usage_log.key_source),
  pre-flight launch (invalid 400 / cap 402 avant débit), pricing beta -50% crédits
  si BYOK complet + bonus one-time 200 crédits, compliance (colonne "API key" per-scan,
  registre intact), env-patch contenu seo_llm. Chaque tranche déployée + smoke prod.
- **Fix critique post-ship** (`f3923a8`) : LLMClient("gemini", api_key) du submodule
  IGNORAIT la clé passée (rotator interne env plateforme prioritaire - prouvé clé
  bidon qui générait). create_llm_client force désormais la clé AUTORITAIRE
  (_rotator=None) ; les 3 briefs passent par la factory. Règle : toute construction
  gemini SaaS = create_llm_client, re-tester (clé bidon → API_KEY_INVALID) après
  chaque bump du submodule.
- **Pool Gemini réduit à 1 clé** (AIzaSyDES..., projet tier payant) sur décision user :
  3 lignes .env VPS + containers recréés + appel réel vérifié. Le code du pool RESTE
  (dégrade seul, garde le parking spend-cap). L'ancienne clé AIzaSyC... est révocable
  dans Google AI Studio.

Détail complet : mémoires `project_byok` + `project_todo_tracker` (section 2026-07-16).

## Prompt à coller pour la prochaine session

```
Reprise sen-ai.fr. Lis project_todo_tracker.md, puis les mémoires
project_act_scope_plan (LE plan P3, design locked 2026-06-12),
project_byok (sélecteur cadré + foot-guns + harnais smoke),
project_gemini_key_pool (pool réduit à 1 clé le 2026-07-16).

MISSION en 2 temps, effort max :
1. ACT-SCOPE P3 - ères de modèles (~1,5j), OBLIGATOIRE avant tout switch
   de modèle. Spec déjà validée : summary["models"]={provider: model} à la
   complétion de run_llm_tests + script backfill depuis les rows existantes
   (scan_llm_results.model) ; points de trend /results/aggregated et
   workspaces/overview portent models + model_changed, delta=null +
   delta_reason="model_changed" au franchissement ; front = marqueur sur le
   point de sparkline + chip "AI models updated" à la place du delta
   (pattern masque sentiment, commit 1417ac2) ; paragraphe model eras dans
   /methodology EN+FR. Cas de test réel : lignée demo Voltaic (seed 4 AIs
   vs scans récents 1-2 providers).
2. PUIS sélecteur de version de modèle par scan (seulement si P3 déployé
   et validé) - décisions déjà prises, ne pas relitiger : gated BYOK
   complet uniquement (jamais sur clés plateforme sans pricing pondéré),
   allowlist courte par provider, choisi au scan root + hérité au rescan
   (lignée homogène), affiché dans le rapport compliance per-scan (la
   colonne "API key" a préparé le terrain), le changement de modèle crée
   une frontière d'ère P3.

Commence par un PLAN court que je valide (EnterPlanMode) : le design P3
est locked, le plan doit surtout mapper les fichiers exacts + le câblage
du sélecteur.

Foot-guns : jointures cross-scan par TEXTE normalisé, jamais question_id
(copies rescan + imports root-pointing) ; ne pas casser le deep-link
?run=X ; harnais smoke réutilisable = org demo 16cedfcc + Voltaic Motors
(5 questions, scan 2fc8f2b4) avec cleanup complet (cf project_byok §9) ;
deploy = scp + docker rebuild + restart nginx + smoke synchrone après
CHAQUE tranche ; diff api/.env et worker/.env vs .env.save avant deploy ;
update project_todo_tracker.md après chaque tranche. No em-dash, UI en
anglais.

Si temps restant en fin de session : les 9 alertes Dependabot GitHub
(4 high) réapparues au push du 2026-07-16.
```

## Backlog derrière (après P3 + sélecteur)

- E2e génération contenu BYOK réelle (aucun item 'identified' dans l'org demo au ship).
- Runtime Mistral (clés stockables/validées, dormantes - "Stored, not used yet").
- agent.py chatbot BYOK (+ logging llm_usage obligatoire dans le même change).
- Drop table dormante client_api_keys + move PoolRotatingGeminiClient près du pool.
- Flagship cocon /guides (T+14 dépassé : déclarer sen-ai.fr comme client, mesurer le lift).
- Transfert registrar .fr → OVH avant le 23/09/2026 (authcode demandé le 28/06).

## Pièges permanents (rappels)

- api/.env et worker/.env : diff vs .env.save avant deploy ; backup avant édition ;
  éditer le .env LOCAL du repo ne fait RIEN en prod.
- Après `docker compose up -d` qui recrée un container → TOUJOURS `restart nginx`.
- Un `docker compose restart` ne recharge PAS le .env - il faut `up -d` (recréation).
- VPS legacy file shadow : tout DELETE de fichier api/worker doit être mirroré en ssh rm.
- Submodule worker/seo_llm : JAMAIS éditer dedans, toujours wrapper côté SaaS.
- Tout bump astro/adapter : tester `node dist/server/entry.mjs` local AVANT deploy.
