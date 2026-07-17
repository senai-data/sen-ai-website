# Reprise session sen-ai-website - post 2026-07-17 (invitations + forgot-password)

## Bilan session 2026-07-17 (2 commits, tout déployé + smoke prod)

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
