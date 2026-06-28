#!/bin/bash
# Restauration de la base Postgres sen-ai depuis un dump (pendant de deploy/backup.sh).
# Source du dump, par ordre de priorité :
#   1) chemin passé en argument :            deploy/restore.sh /opt/backups/senai_XXXX.sql.gz
#   2) dernier dump sur Cloudflare R2 (si deploy/backup.env présent)
#   3) dernier dump local dans /opt/backups
#
# DESTRUCTIF : DROP puis CREATE de la base `senai`, restauration par-dessus.
# Garde-fou : ne s'exécute QUE si CONFIRM=yes est passé en variable d'environnement.
#   CONFIRM=yes /root/sen-ai-website/deploy/restore.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"          # -> /root/sen-ai-website
BACKUP_DIR="${SENAI_BACKUP_DIR:-/opt/backups}"
ENV_FILE="$REPO_DIR/deploy/backup.env"
ARG_DUMP="${1:-}"

if [ "${CONFIRM:-}" != "yes" ]; then
  echo "REFUS : opération destructive (écrase la base senai)." >&2
  echo "Relancer avec :  CONFIRM=yes $0 [chemin_dump.sql.gz]" >&2
  exit 1
fi

cd "$REPO_DIR"

# 1. Déterminer la source du dump.
if [ -n "$ARG_DUMP" ]; then
  SRC="$ARG_DUMP"
elif [ -f "$ENV_FILE" ]; then
  echo "[restore] récupération du dernier dump depuis R2..."
  set -a; . "$ENV_FILE"; set +a
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
  LATEST="$(rclone lsf "R2:$R2_BUCKET/" | grep '\.sql\.gz$' | sort | tail -1)"
  [ -n "$LATEST" ] || { echo "[restore] aucun dump .sql.gz dans R2:$R2_BUCKET" >&2; exit 1; }
  mkdir -p "$BACKUP_DIR"
  rclone copy "R2:$R2_BUCKET/$LATEST" "$BACKUP_DIR/"
  SRC="$BACKUP_DIR/$LATEST"
else
  SRC="$(ls -t "$BACKUP_DIR"/senai_*.sql.gz 2>/dev/null | head -1 || true)"
fi

[ -n "${SRC:-}" ] && [ -f "$SRC" ] || { echo "[restore] dump introuvable (SRC='$SRC')" >&2; exit 1; }
echo "[restore] source = $SRC ($(du -h "$SRC" | cut -f1))"

# 2. Couper les consommateurs pour libérer les connexions à la base.
echo "[restore] arrêt api / worker / worker-content..."
docker compose stop api worker worker-content

# 3. DROP + CREATE de la base (idempotent : marche sur base vierge OU peuplée).
echo "[restore] recréation de la base senai..."
docker compose exec -T postgres psql -U senai -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='senai' AND pid<>pg_backend_pid();" >/dev/null
docker compose exec -T postgres psql -U senai -d postgres -c "DROP DATABASE IF EXISTS senai;"
docker compose exec -T postgres psql -U senai -d postgres -c "CREATE DATABASE senai OWNER senai;"

# 4. Restauration du dump (pg_dump plain, gzip).
echo "[restore] restauration en cours..."
gunzip -c "$SRC" | docker compose exec -T postgres psql -U senai -d senai >/dev/null

# 5. Redémarrage applicatif + nginx (cache DNS interne, cf project_vps_deploy_process).
echo "[restore] redémarrage des services..."
docker compose up -d api worker worker-content
docker compose restart nginx

echo "[restore] OK -> base senai restaurée depuis $(basename "$SRC")"
echo "[restore] vérifier : curl -s -o /dev/null -w '%{http_code}' https://sen-ai.fr/"
