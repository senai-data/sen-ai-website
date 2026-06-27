#!/bin/bash
# Sauvegarde quotidienne de la base Postgres sen-ai (porté de storva/deploy/backup.sh).
# - dump local gzip + rotation (RETENTION_DAYS) ;
# - push HORS-VPS vers Cloudflare R2 si deploy/backup.env est présent (rclone, S3-compat) ;
# - rétention R2 identique (supprime les dumps trop vieux) ;
# - garde-fou anti-dépassement du tier gratuit R2 : alerte si le bucket dépasse un seuil.
# Installé en cron sur le VPS (voir docs/migration_o2switch_to_cloudflare.md). À lancer de n'importe où :
#   /root/sen-ai-website/deploy/backup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"          # -> /root/sen-ai-website
BACKUP_DIR="${SENAI_BACKUP_DIR:-/opt/backups}"
RETENTION_DAYS="${SENAI_BACKUP_RETENTION:-14}"
ALERT_THRESHOLD_GB="${SENAI_BACKUP_ALERT_GB:-5}"      # alerte à 50% du tier gratuit R2 (10 Go)
ENV_FILE="$REPO_DIR/deploy/backup.env"

mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/senai_$(date +%F_%H%M).sql.gz"

cd "$REPO_DIR"
docker compose exec -T postgres pg_dump -U senai senai | gzip > "$OUT"

# Rotation locale.
find "$BACKUP_DIR" -name 'senai_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
echo "[backup] $(date -Is) local -> $OUT ($(du -h "$OUT" | cut -f1))"

# Push hors-VPS vers R2 (seulement si configuré).
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
  # Remote rclone défini entièrement par variables d'env (pas de rclone.conf à maintenir).
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
  export RCLONE_CONFIG_R2_ACL=private
  # Token scopé à un seul bucket -> pas de HeadBucket/ListBuckets autorisé.
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

  rclone copy "$OUT" "R2:$R2_BUCKET/"
  echo "[backup] push R2 ok -> R2:$R2_BUCKET/$(basename "$OUT")"

  # Rétention R2 : supprime les dumps de plus de RETENTION_DAYS (le stockage reste borné).
  rclone delete "R2:$R2_BUCKET/" --min-age "${RETENTION_DAYS}d" || true

  # Garde-fou anti-dépassement : alerte si le bucket dépasse le seuil (bien avant 10 Go).
  BYTES=$(rclone size "R2:$R2_BUCKET" --json 2>/dev/null | grep -o '"bytes":[0-9]*' | head -1 | cut -d: -f2 || echo 0)
  THRESHOLD=$(( ALERT_THRESHOLD_GB * 1024 * 1024 * 1024 ))
  if [ "${BYTES:-0}" -gt "$THRESHOLD" ]; then
    GB=$(awk "BEGIN{printf \"%.2f\", ${BYTES}/1073741824}")
    echo "[backup] ALERTE: bucket R2 = ${GB} Go (> ${ALERT_THRESHOLD_GB} Go) -- vérifier la rétention"
    # Email best-effort (adapter l'import si besoin ; ne bloque jamais le backup).
    docker compose exec -T api python -c "from services.email import send_email; send_email('data@sen-ai.fr', '[sen-ai] Alerte backups R2 - ${GB} Go', '<p>Le bucket R2 atteint <b>${GB} Go</b> (seuil ${ALERT_THRESHOLD_GB} Go ; tier gratuit Cloudflare = 10 Go). Vérifier que la rétention fonctionne.</p>')" 2>/dev/null || true
  fi
else
  echo "[backup] (R2 non configuré : deploy/backup.env absent -> backup local seulement)"
fi
