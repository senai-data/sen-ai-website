#!/bin/bash
# Garde-fou anti-saturation disque (incident récurrent "disk full -> deploy échoue").
# Le cache de build Docker gonfle (24 Go observés au 28/06) et finit par remplir / a 100%,
# ce qui fait échouer silencieusement les `docker compose build`. Ce script purge le cache
# de build + les images orphelines, mais SEULEMENT si le disque dépasse un seuil, pour ne
# pas jeter le cache à chaque passage (les builds restent rapides tant qu'on a de la marge).
# Installé en cron hebdo sur le VPS. Lançable à la main :  /root/sen-ai-website/deploy/docker-prune.sh
set -euo pipefail

THRESHOLD_PCT="${SENAI_PRUNE_THRESHOLD:-70}"   # purge si l'usage de / atteint ce %

USED="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
if [ "${USED:-0}" -ge "$THRESHOLD_PCT" ]; then
  BEFORE="$(df -h / | tail -1 | awk '{print $5}')"
  docker builder prune -af  >/dev/null 2>&1 || true
  docker image prune   -af  >/dev/null 2>&1 || true
  AFTER="$(df -h / | tail -1 | awk '{print $5}')"
  echo "[docker-prune] $(date -Is) PURGE (disque ${BEFORE} -> ${AFTER}, seuil ${THRESHOLD_PCT}%)"
else
  echo "[docker-prune] $(date -Is) skip (disque ${USED}%, sous le seuil ${THRESHOLD_PCT}%)"
fi
