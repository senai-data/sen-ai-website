# sen-ai.fr - Kit de reprise après sinistre (disaster recovery)

> But : reconstruire entièrement sen-ai.fr sur un VPS vierge si le serveur est perdu.
> Rédigé depuis l'état réel de la prod (inventaire VPS du 2026-06-28), pas un gabarit générique.
> **Le fichier local est la source de vérité.** Le VPS n'est pas un repo git (cf [[project_vps_deploy_process]]).

## 0. En une phrase

Le code est sur **GitHub**, les données sont dans **Postgres** (sauvegardé chaque nuit sur **Cloudflare R2**), les secrets sont dans des `.env` **git-ignorés** (donc à sauvegarder hors-VPS séparément). Reconstruire = VPS vierge -> Docker -> clone -> restaurer les `.env` -> `docker compose up -d --build` -> `restore.sh` -> repointer le DNS Cloudflare.

## 1. Inventaire (où vit quoi)

| Brique | Emplacement | Sauvegardé ? |
|---|---|---|
| **Code** | GitHub `github.com/senai-data/sen-ai-website` (branche `master`) | ✅ git |
| **Compute** | VPS Hetzner, Ubuntu 24.04, IP `135.181.156.218`, `/root/sen-ai-website` | Snapshots Hetzner (à vérifier activés) |
| **Base de données** | container `senai-postgres` (postgres:16-alpine), volume `sen-ai-website_postgres_data` | ✅ dump nuit -> R2 + local `/opt/backups` |
| **Fichiers rapports** | bind mount hôte `/opt/sen-ai/reports` (~8 Mo) | ⚠️ **NON** (cf gaps §6) |
| **Secrets** | `api/.env`, `worker/.env`, `deploy/backup.env` (git-ignorés) | ⚠️ **à sauvegarder hors-VPS à la main** (§4) |
| **Certificats TLS** | `/etc/letsencrypt/live/sen-ai.fr` (Let's Encrypt, renouvellement webroot) | ⚠️ NON (réémissible, cf §5.7) |
| **DNS + proxy + WAF** | Cloudflare (NS `kipp`/`robin.ns.cloudflare.com`, `@`/`www` proxy orange, SSL Full strict) | Config côté Cloudflare |
| **Registrar `.fr`** | o2switch -> transfert OVH en cours (NS restent Cloudflare) | - |

Services Docker (6 containers, cf `docker-compose.yml`) : `nginx` (alpine, 80/443) · `astro` (build `Dockerfile.astro`) · `api` (FastAPI, `api/.env`) · `worker` (scan, `WORKER_ID=worker-scan`) · `worker-content` (article/FAQ/suggest_media, `WORKER_ID=worker-content`) · `postgres`.

## 2. Sauvegardes (état réel)

- **Quoi** : `pg_dump` de la base `senai`, gzip, par `deploy/backup.sh`.
- **Quand** : cron root **02:30** chaque nuit -> `/var/log/senai-backup.log`.
- **Où** : local `/opt/backups/senai_AAAA-MM-JJ_HHMM.sql.gz` **+** Cloudflare R2 bucket `senai-backups` (push rclone, remote défini 100 % par variables d'env, pas de `rclone.conf`).
- **Rétention** : 14 jours (local et R2). Alerte si le bucket dépasse 5 Go (tier gratuit R2 = 10 Go).
- **Taille** : DB ~280 Mo -> dump ~31 Mo.

**Vérifier que les backups tournent** (à faire de temps en temps) :
```bash
ssh -i ~/.ssh/id_ed25519 root@135.181.156.218
tail -5 /var/log/senai-backup.log
ls -la /opt/backups | tail -3
# Lister R2 (env sourcé) :
cd /root/sen-ai-website && set -a; . deploy/backup.env; set +a
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
  RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
rclone lsl "R2:$R2_BUCKET/" | sort | tail -3
```

## 3. Restauration de la base seule (rollback ou DR)

Script `deploy/restore.sh` (pendant de `backup.sh`). **Destructif** : DROP + CREATE de `senai` puis restauration. Prend le dernier dump R2 par défaut, ou un fichier explicite.

```bash
# Dernier dump R2 :
CONFIRM=yes /root/sen-ai-website/deploy/restore.sh
# Un dump précis :
CONFIRM=yes /root/sen-ai-website/deploy/restore.sh /opt/backups/senai_2026-06-28_0230.sql.gz
```
Le script coupe `api`/`worker`/`worker-content`, recrée la base, restaure, puis relance les services + `restart nginx`. Vérifier ensuite un `200` sur `https://sen-ai.fr/`.

## 4. Secrets à sauvegarder HORS-VPS (critique)

Les `.env` sont **git-ignorés** : ils ne sont NI sur GitHub NI dans les dumps Postgres. Sans eux, un VPS reconstruit ne redémarre pas. À exporter chiffrés hors-VPS (coffre + copie locale) et à re-vérifier après chaque ajout de provider.

Récupérer les fichiers depuis le VPS :
```bash
scp -i ~/.ssh/id_ed25519 root@135.181.156.218:/root/sen-ai-website/api/.env       ./secrets/api.env
scp -i ~/.ssh/id_ed25519 root@135.181.156.218:/root/sen-ai-website/worker/.env    ./secrets/worker.env
scp -i ~/.ssh/id_ed25519 root@135.181.156.218:/root/sen-ai-website/deploy/backup.env ./secrets/backup.env
```

Inventaire des clés (valeurs = dans le coffre, jamais ici) :

- **`api/.env`** : `JWT_SECRET`, **`OAUTH_FERNET_KEY`** (🔴 le plus critique), `INTERNAL_SERVICE_TOKEN`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEYS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_GOOGLE_REDIRECT_URI`, `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `FRONTEND_URL`, `REGISTRATION_OPEN`.
- **`worker/.env`** : **`DATABASE_URL`** (contient le mot de passe Postgres), **`OAUTH_FERNET_KEY`** (doit être IDENTIQUE à celui de l'api), `INTERNAL_SERVICE_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GEMINI_API_KEYS`, `GEO_GROUNDING_PROVIDER`, `BABBAR_API_KEY`, `HALOSCAN_API_KEY`, `YOURTEXTGURU_API_KEY` / `YTG_LANGUAGE` / `YTG_RATE_LIMIT`, `LINKFINDER_EMAIL`, `LINKFINDER_PASSWORD`, `SERPER_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `LLM_DAILY_COST_CAP_USD`, `POLL_INTERVAL`, `HEALTHCHECK_WORKER_URL`, `HEALTHCHECK_T14_URL`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `WORKER_ID`.
- **`deploy/backup.env`** : `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET`.

> 🔴 **`OAUTH_FERNET_KEY`** : si perdu, toutes les connexions OAuth chiffrées en base deviennent illisibles (déconnexion de tous les comptes Google liés). Irrécupérable. La même valeur doit être dans `api/.env` ET `worker/.env`.
> ⚠️ **Pas de `.env` racine** sur le VPS : le `POSTGRES_PASSWORD` du `docker-compose.yml` retombe sur son défaut `senai-change-in-prod`. Le mot de passe réel de la base est donc celui encodé dans `worker/.env -> DATABASE_URL` ; il doit rester cohérent avec `POSTGRES_PASSWORD`. Noter la valeur dans le coffre.
> ⚠️ **`api/.env` perd des variables silencieusement** (cf [[project_prod_env_hazard]]). Garder `api/.env.save` à jour et diff avant tout redéploiement.

## 5. Reconstruction complète sur un VPS vierge

```bash
# 1. Provisionner un VPS Hetzner Ubuntu 24.04, ajouter la clé SSH ~/.ssh/id_ed25519, se connecter en root.

# 2. SWAP (le VPS actuel n'en a PAS -> risque OOM au build astro). En créer 2 Go :
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 3. Docker + plugin compose :
apt-get update && apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 4. Cloner le code (les scripts sont versionnés en 644 -> rendre exécutables) :
git clone https://github.com/senai-data/sen-ai-website.git /root/sen-ai-website
cd /root/sen-ai-website
chmod +x deploy/*.sh

# 5. Restaurer les secrets (depuis le coffre, cf §4) :
#    api/.env , worker/.env , deploy/backup.env  (chmod 600 deploy/backup.env)

# 6. Créer les répertoires hôte montés par compose :
mkdir -p /opt/sen-ai/reports /opt/backups /var/www/certbot

# 7. Certificats TLS (Cloudflare est en Full strict -> l'origine A BESOIN d'un cert valide) :
#    Option A : restaurer /etc/letsencrypt depuis une sauvegarde si disponible.
#    Option B : réémettre via certbot webroot. Mettre temporairement l'enregistrement DNS
#               en "DNS only" (nuage gris) côté Cloudflare pour laisser passer le http-01,
#               émettre, puis repasser en proxy orange :
#       apt-get install -y certbot
#       certbot certonly --webroot -w /var/www/certbot -d sen-ai.fr -d www.sen-ai.fr
#    Option C (plus robuste pour la DR) : remplacer Let's Encrypt par un Cloudflare Origin
#               Certificate (15 ans) et le poser dans /etc/letsencrypt/live/sen-ai.fr/.

# 8. Build + démarrage :
docker compose up -d --build

# 9. Restaurer la base depuis R2 :
CONFIRM=yes deploy/restore.sh

# 10. (si sauvegardés) restaurer les fichiers rapports dans /opt/sen-ai/reports (cf gaps §6).

# 11. Réinstaller le cron de backup :
( crontab -l 2>/dev/null; echo '30 2 * * * /root/sen-ai-website/deploy/backup.sh >> /var/log/senai-backup.log 2>&1' ) | crontab -

# 12. Repointer le DNS : Cloudflare -> DNS -> enregistrement A `@` (et `www`) vers la
#     nouvelle IP du VPS, proxy ORANGE. SSL/TLS reste "Full (strict)". Le patch real_ip
#     Cloudflare est déjà dans nginx/nginx.conf (versionné).

# 13. Vérifier :
curl -s -o /dev/null -w '%{http_code}\n' https://sen-ai.fr/         # 200
curl -s -o /dev/null -w '%{http_code}\n' https://sen-ai.fr/guides/  # 200
#   + login, lancement d'un scan, réception email transactionnel.
```

## 6. Gaps connus / risques (à traiter)

1. **`/opt/sen-ai/reports` (8 Mo) n'est PAS sauvegardé** : les rapports publiés vivent sur l'hôte, hors dump Postgres. En cas de perte VPS, les fichiers déjà publiés disparaissent (re-générables depuis la base, mais URLs cassées en attendant). *Fix possible* : ajouter à `backup.sh` un `tar czf` de `/opt/sen-ai/reports` poussé sur R2, OU accepter le caractère régénérable.
2. **`/etc/letsencrypt` n'est pas sauvegardé** : certs réémissibles (§5.7), mais prévoir le chemin certbot/Cloudflare Origin pour éviter une coupure Full strict pendant la DR.
3. **Pas de swap** sur le VPS actuel : risque OOM au build astro (déjà à l'origine d'un incident 502). La reconstruction (§5.2) en ajoute ; envisager de le faire aussi sur le VPS courant.
4. **Volume Postgres orphelin** : `senai_postgres_data` (48 Mo, 0 lien, vestige d'un ancien nom de projet) coexiste avec l'actif `sen-ai-website_postgres_data`. Vérifier qu'il est bien inutilisé puis `docker volume rm senai_postgres_data` pour lever l'ambiguïté.
5. **Snapshots Hetzner** : confirmer qu'ils sont activés (fallback ultime, restaure l'OS + les volumes d'un coup).
6. **HaloScan / YourTextGuru / Link Finder** sont câblés (clés dans `worker/.env`) mais absents du registre `src/data/subprocessors.ts` : voir le TODO conformité (les ajouter au registre ou acter le cadrage « signaux SEO publics, pas de donnée perso »).

## 7. Inventaire des accès (à garder dans le coffre)

Hetzner (VPS + snapshots) · Cloudflare (DNS/proxy/R2/Analytics) · OVH (registrar `.fr`, en cours) · GitHub `senai-data/sen-ai-website` · M365 admin (email) · Google Cloud (OAuth client) · Stripe · clé SSH `~/.ssh/id_ed25519`. Fournisseurs API : OpenAI, Anthropic, Google Gemini, Babbar, HaloScan, YourTextGuru, Link Finder, Serper, Resend.
