# Reprise session sen-ai-website - post 2026-07-18 (P4 streak SHIPPED)

## Bilan session P4 (2026-07-18) - act-scope CLOS hors P5/P6

- **P4 streak opportunities SHIPPED + validé prod** (détail : project_todo_tracker
  + project_act_scope_plan). Migration 061 (status/streak + colonne **provider**
  ajoutée à la spec - la clé de streak l'exige, le handler ne la persistait pas),
  handler prev-scan par (texte normalisé, provider) + fallback texte-only legacy,
  GET /opportunities + resolved[] cap 20 + previous_scan_id, actions.astro chips
  "New"/"Since N scans" + panel "Resolved since last scan". Validé sur A-Derma
  run 6 (prev = root importé run_index 5) : 364 persisting / 33 new, synthétiques
  exact-key + isolation provider + mismatch→resolved, cleanup vérifié.
- Session parallèle TRÈS active le même matin (commits ac8711e P5c cache,
  2a3d3d2 questions 42MB→865KB, 20b345c top-30 materialization cap, 6886557
  guides). Arbre partagé : le code P4 de scans.py est parti dans LEUR ac8711e,
  et mes fichiers stagés (migration 061, models ×2, handler, actions.astro,
  ce fichier) ont été balayés dans leur 6886557 - le détail P4 est dans le
  tracker, pas dans un commit dédié.
- v2 différée : bump de priorité si streak>=3. Suivi : get_opportunities pourrait
  honorer `?provider=` (le front l'envoie déjà, trivial avec la colonne).

## Prompt à coller pour la prochaine session

```
Reprise sen-ai.fr. Lis project_todo_tracker.md (sections 2026-07-18) d'abord.

Candidats de session (par priorité) :
1. Réécriture SQL de /results/aggregated (~4s de boucles Python par lignée à
   froid, le cache P5c ne couvre que les hits chauds) - lire la section perf
   du tracker 2026-07-18 avant.
2. Module Placements (suivi articles publiés) - diagnostic fait, cf
   project_placements_module_diagnostic : watchlist URLs + matcher zéro-LLM.
3. Redesign results/overview - cf project_postscan_reporting_audit (12 gaps
   data + bug Top competitor=ameli.fr). Lire l'audit AVANT.
4. Backlog BYOK : e2e contenu réel, runtime Mistral, agent.py chatbot.
(questions 42MB et top-N suggestions : soldés le 18/07 par la session
parallèle, commits 2a3d3d2 + 20b345c.)

Foot-guns : deploy = scp + rebuild + up -d + restart nginx + smoke synchrone ;
diff .env vs .env.save avant deploy ; update tracker après chaque tranche.
No em-dash, UI en anglais.
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
- Invitations : les routes token-scoped ont une route littérale /received -
  toute nouvelle route littérale doit rester déclarée AVANT /{invite_token}/*.
- Boot worker : discover_media_catalog peut bloquer la queue (Babbar quota) -
  pattern Sprint 10, dédupliquer les doublons pending après rebuilds successifs.
- Jointures cross-scan opportunities/kanban : TOUJOURS (texte normalisé,
  provider), jamais question_id (rescans copient les questions, imports
  pointent le root).
