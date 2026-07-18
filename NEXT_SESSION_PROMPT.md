# Reprise session sen-ai-website - post 2026-07-18 (perf sweep) - PROCHAINE SESSION : P4

## Prompt à coller pour la session P4 (streak opportunities, ~2j)

```
Reprise sen-ai.fr - objectif de session : P4 streak opportunities.

Lis d'abord project_act_scope_plan.md (section P4 + foot-guns) puis
project_todo_tracker.md (sections 2026-07-17/18 : perf + accès implicite).

Spec P4 (déjà actée avec moi le 2026-06-12) :
1. Migration SQL : scan_opportunities + status TEXT DEFAULT 'new' + streak INT
   DEFAULT 1 (+ ORM models.py). ⚠️ Le plan dit "migration 059" mais il date
   d'avant BYOK - 057/058/060 sont prises, vérifier le prochain numéro libre
   dans api/migrations/ et l'appliquer sur le VPS (psql in-container).
2. worker/handlers/generate_opportunities.py : charger les opportunities du
   scan complété PRÉCÉDENT du lineage, indexées par (texte question normalisé
   lower/trim/collapse-ws, provider). Match → status='persisting',
   streak=prev+1 ; sinon 'new'.
3. GET /scans/{id}/opportunities : ajouter resolved[] = clés du précédent
   absentes du courant (cap 20).
4. actions.astro : chips "New" / "Since N scans" + panel "Resolved since last
   scan" (Peak-End). v1 = affichage pur, PAS de bump de priorité (v2 différée).

Foot-guns critiques :
- Jointure cross-scan par TEXTE normalisé, JAMAIS question_id (le rescan copie
  les questions avec de nouveaux ids ; l'import historique pointe vers le root).
- La clé de streak inclut provider (un trou gemini ≠ un trou openai).
- Cross-ère de modèles = FEATURE : une opportunity absente avant ET après un
  changement de modèle = trou structurel - ne PAS suspendre le streak à la
  frontière P3.
- get_opportunities est un `def` synchrone depuis le 18/07 (perf event-loop) -
  le garder def.
- actions.astro utilise effectiveScanId (résolution act-scan P1 déjà en place).
- generate_opportunities crée du netlinking sans filtre intent (gap Phase B
  connu, diagnostiqué, NE PAS traiter dans cette session).
- POST_SCAN_AUDIT_JOB_TYPES : ne pas casser la chaîne post-scan (cf Sprint 7).

Validation : lignée réelle 2+ runs (Voltaic demo, ou lignées PF - attention
imports antedatés : le ROOT porte run_index 5, children 1-4). Rescan de test
ou rows SQL synthétiques pour vérifier persisting/new/resolved, cleanup après.
Smoke prod par tranche (deploy = scp + docker compose build/up -d + restart
nginx). Update project_todo_tracker après chaque tranche, commit/push à la fin.
No em-dash, UI en anglais.
```

## Bilan sessions 2026-07-17/18 (détail complet dans project_todo_tracker)

- Invitations : bandeau reçues + auto-grant admin + accès implicite owner/admin.
- Workspace management : delete self-service + rename accessibles en multi-org/
  multi-client + org PF convertie standard.
- Perf sweep (RUM Cloudflare branché, token local) : dashboard 1,5s→0,8s,
  content 1,95s→1,2s + payload kanban 1,8MB→82KB, results 12,7s→5,7s,
  citations 8s→2,4s, questions TTFB 14,7s→0,09s (streaming), assets edge-cached.
  Racine systémique : endpoints async def + SQLAlchemy sync bloquaient l'event
  loop - 4 convertis def. Backlog : SQL rewrite aggregated, questions 42MB,
  top-N suggestions, distribution (~20 visites humaines/17j).

# Archive - post 2026-07-17 (invitations + workspace management)

## Bilan session 2026-07-17 - tranche 2 (workspace management, commit 5d45f28)

- **DELETE /api/clients/{id}** : org owner/admin only, refus 409 si scans ou
  credit rows (self-service = workspaces vides créés par erreur uniquement).
  Déclaré APRÈS la route littérale /active. Smoke : jetable 200, member 403,
  PF 26 scans 409, /active intact.
- **Sidebar : la ligne ACTIVE des dropdowns porte les actions de gestion**
  (elle n'est pas une cible de switch) : org dropdown multi → crayon rename ;
  workspace dropdown multi → crayon + corbeille ; pill mono-client → corbeille
  ajoutée. Avant : aucun rename/delete accessible dès 2+ orgs ou 2+ workspaces.
  Confirm delete inline, erreur 409 affichée sur place.
- **Foot-gun Alpine** : @click.outside déplacé du bouton trigger vers le div
  wrapper des 2 dropdowns, sinon tout clic dans le panneau (input de rename
  inline) fermait le dropdown. Validé en navigateur réel.
- **Org "Pierre Fabre workspace" convertie en org standard** (is_personal=false,
  feu vert user) : c'était l'ex-org personnelle de data@ (backfill C.1) devenue
  de facto partagée (4 membres). Fallback cookie-less testé pour les 4 comptes
  (plus personne n'a d'org personal → union clients OK). Rename d'org autorisé
  sur les orgs personal restantes (gate !is_personal retiré du crayon).
- Workspace "Damien" (erreur user, vide) supprimé manuellement en DB avant que
  le self-service delete n'existe.

## Bilan session 2026-07-17 - tranche 1 (2 commits, tout déployé + smoke prod)

- **fix(auth) forgot-password OAuth-only** (`6aaeecd`) : la branche
  `if user.password_hash` ignorait silencieusement les comptes Google
  (password_hash=None) → aucun email. Ajout branche google_id → email
  "Continue with Google". Anti-énumération préservé (toujours 200).
  Contexte : demo_agency@sen-ai.fr est un compte Google OAuth.
- **feat(invitations) bandeau reçues + auto-grant + redesign Members**
  (`4eba309`) - déclenché par une confusion user réelle en deux temps :
  1. `GET /api/invitations/received` (route littérale AVANT
     /{invite_token}/*) : invitations pending pour l'email du user
     connecté → bandeau dashboard "X invited you to join Y" + bouton vers
     /invite/{token}. Le redirect /welcome (0 scans + 0 orgs) est skippé
     si invitation pending. Avant : la seule surface d'une invitation
     reçue était le lien email (la page members liste les ENVOYÉES).
  2. **Auto-grant à l'accept** : org_role owner/admin →
     `_grant_all_org_clients` (OrgUserClient + UserClient legacy, manager,
     idempotent, jamais de downgrade) dans /accept ET /accept-and-register.
     Un org admin pouvait déjà s'auto-granter via la grille members → le
     "No access" par défaut était de la friction, pas de la sécurité. Les
     invités member restent no-access by design (eux ne peuvent PAS
     s'auto-granter = vraie frontière). Backfill demo_agency fait (manager
     sur Pierre Fabre). E2e prod user jetable : accept → rows auto → clean.
  3. **Redesign /app/org/members** : matrice member × workspace (débordait
     à 7 colonnes) → 1 ligne/membre + chip "N of M workspaces" cliquable
     qui déplie l'édition inline ; invite form replié derrière l'unique
     bouton coral ; search membres ≥4 ; "No workspace access" en amber ;
     em-dashes purgés (members.astro + 3 tooltips Tip.astro).

Détail : mémoires `project_registration_closed` (section "Invitation flow
additions 2026-07-17") + `project_todo_tracker`.

## Prompt à coller pour la prochaine session

```
Reprise sen-ai.fr. Lis project_todo_tracker.md puis, si tu touches aux
invitations/membres, project_registration_closed (section 2026-07-17).

Candidats de session (par priorité) :
1. Accès implicite owner/admin dans services/access.py : un owner/admin
   n'obtient PAS l'accès aux workspaces créés APRÈS son arrivée dans l'org
   (create-client ne grante que le créateur ; l'auto-grant à l'accept ne
   couvre que les workspaces existants à ce moment-là). Si fait, refléter
   l'implicite dans la grille members (ne pas afficher un "No access" qui ment).
   Nit UI connexe : Escape dans l'input de rename inline ne referme pas le
   dropdown (le .stop coupe la propagation vers @keydown.escape.window).
2. Act-scope P4 streak opportunities (~2j, cf project_act_scope_plan) -
   status persisting/new + resolved[], cross-ère = feature.
3. Décision pricing : défaut plateforme OpenAI vers un modèle consommateur
   (gpt-5.5 $5/$30 ou gpt-5.6-luna $1/$6 vs gpt-4.1-mini actuel) - P3
   annotera la frontière tout seul, mais crédits à repondérer d'abord.
4. Backlog BYOK : e2e contenu réel, runtime Mistral, agent.py chatbot,
   drop client_api_keys.
5. Follow-ups UX members (trade-offs notés par la revue) : bulk "grant all
   workspaces", filtre workspaces dans l'expansion si org >30 workspaces.

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
