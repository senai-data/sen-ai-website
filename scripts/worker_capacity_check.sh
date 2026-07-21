#!/usr/bin/env bash
# worker_capacity_check.sh
#
# Weekly capacity watchdog for the worker queue (project_worker_queue_scaling).
# Tranches 1+2 (priority band + sweep isolation) removed the "background sweep
# blocks a user scan" bug. Tranches 3 (scale worker=N) and 4 (LLM rate-limit /
# BYOK) are "on trigger" - this script IS the trigger. It measures the two
# signals and emails ONLY when a threshold is crossed (silent otherwise).
#
#   T3 signal - throughput wall on the single scan-worker:
#     * run_llm_tests that waited > 10 min to START in the last 7d (>=2 = a
#       user scan is regularly queued behind another running scan, since sweeps
#       are now isolated a long wait can only mean another run_llm_tests), OR
#     * >=3 run_llm_tests pending right now.
#     -> Action: audit handler races FIRST (cleanup_brands / detect_competitors
#        on scan_brand_classifications, generate_opportunities delete+recreate,
#        materialize_content_items dedup, LLM $1/day cap TOCTOU - cf
#        project_worker_split), remove `container_name: senai-worker` from
#        docker-compose.yml, then `docker compose up -d --scale worker=3`.
#
#   T4 signal - LLM provider rate-limiting:
#     * >=1 run_llm_tests failed on a rate/quota/429 message in the last 7d.
#     -> Action: push BYOK per org (offloads onto the client's quota) + backoff
#        on the shared Gemini pool (project_gemini_key_pool / project_byok).
#
# Cron (weekly, Monday 08:00 server time):
#   0 8 * * 1 /root/sen-ai-website/scripts/worker_capacity_check.sh >> /root/sen-ai-website/scripts/worker_capacity_check.log 2>&1
#
# Flags:
#   --test-email   Send a test email to prove the Resend channel, then exit.

set -euo pipefail

REPO_DIR="/root/sen-ai-website"
ENV_FILE="$REPO_DIR/api/.env"
PG_CONTAINER="senai-postgres"
ALERT_TO="data@sen-ai.fr"        # change here if alerts should go elsewhere

# Thresholds (bump these as real traffic grows).
T3_SLOW_STARTS_MAX=1             # alert when slow_starts_7d exceeds this
T3_PENDING_MAX=2                 # alert when pending_now exceeds this
T4_RATELIMIT_MAX=0               # alert when llm_ratelimit_7d exceeds this

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# --- read Resend creds from api/.env (never hard-coded here) ---
get_env() {
  local v
  v=$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//") || v=""
  printf '%s' "$v"
}
RESEND_API_KEY="$(get_env RESEND_API_KEY)"
RESEND_FROM="$(get_env RESEND_FROM_EMAIL)"

send_email() {
  # $1 = subject, $2 = text body (may contain literal \n escapes)
  local subject="$1" body="$2"
  if [ -z "$RESEND_API_KEY" ] || [ -z "$RESEND_FROM" ]; then
    echo "$(ts) [worker-capacity] WARN: Resend creds missing in $ENV_FILE - cannot email. Subject was: $subject"
    return 0
  fi
  local payload
  payload=$(printf '{"from":"%s","to":["%s"],"subject":"%s","text":"%s"}' \
            "$RESEND_FROM" "$ALERT_TO" "$subject" "$body")
  local http
  http=$(curl -s -o /tmp/wcc_resend.out -w '%{http_code}' -X POST https://api.resend.com/emails \
         -H "Authorization: Bearer $RESEND_API_KEY" \
         -H "Content-Type: application/json" \
         --data "$payload") || http="000"
  if [ "$http" = "200" ] || [ "$http" = "201" ]; then
    echo "$(ts) [worker-capacity] email sent to $ALERT_TO ($http)"
  else
    echo "$(ts) [worker-capacity] WARN: Resend returned $http: $(cat /tmp/wcc_resend.out 2>/dev/null | head -c 300)"
  fi
}

if [ "${1:-}" = "--test-email" ]; then
  send_email "sen-ai worker capacity - test email" \
    "This is a test of the weekly worker capacity watchdog channel.\nIf you got this, alerts will reach you when a T3/T4 threshold is crossed.\nSent $(ts) from worker_capacity_check.sh."
  exit 0
fi

# --- measure the two signals in one query ---
read_signals() {
  docker exec "$PG_CONTAINER" psql -U senai -d senai -tA -F'|' -c "
    SELECT
      (SELECT count(*) FROM jobs
         WHERE job_type='run_llm_tests' AND started_at IS NOT NULL
           AND created_at > now() - interval '7 days'
           AND started_at - created_at > interval '10 minutes'),
      (SELECT count(*) FROM jobs
         WHERE job_type='run_llm_tests' AND status='pending'),
      (SELECT count(*) FROM jobs
         WHERE job_type='run_llm_tests' AND status='failed'
           AND created_at > now() - interval '7 days'
           AND (result->>'user_message' ILIKE '%rate%'
                OR result->>'user_message' ILIKE '%quota%'
                OR result->>'user_message' ILIKE '%429%'));
  "
}

RAW="$(read_signals | tr -d '[:space:]')" || RAW=""
SLOW="${RAW%%|*}"; REST="${RAW#*|}"
PENDING="${REST%%|*}"; RATELIMIT="${REST#*|}"

# Guard against empty/parse failure
if ! [[ "$SLOW" =~ ^[0-9]+$ && "$PENDING" =~ ^[0-9]+$ && "$RATELIMIT" =~ ^[0-9]+$ ]]; then
  echo "$(ts) [worker-capacity] ERROR: could not parse signals (raw='$RAW')"
  exit 1
fi

echo "$(ts) [worker-capacity] slow_starts_7d=$SLOW pending_now=$PENDING llm_ratelimit_7d=$RATELIMIT"

BREACH=""
if [ "$SLOW" -gt "$T3_SLOW_STARTS_MAX" ] || [ "$PENDING" -gt "$T3_PENDING_MAX" ]; then
  BREACH="${BREACH}T3 (throughput): slow_starts_7d=$SLOW (max $T3_SLOW_STARTS_MAX), pending_now=$PENDING (max $T3_PENDING_MAX). Consider scaling the scan-worker - AUDIT handler races first (see project_worker_split), remove container_name from docker-compose.yml, then docker compose up -d --scale worker=3.\n\n"
fi
if [ "$RATELIMIT" -gt "$T4_RATELIMIT_MAX" ]; then
  BREACH="${BREACH}T4 (LLM rate-limit): $RATELIMIT run_llm_tests failed on rate/quota/429 in 7d. Push BYOK per org + backoff on the shared Gemini pool (project_byok / project_gemini_key_pool).\n\n"
fi

if [ -n "$BREACH" ]; then
  send_email "sen-ai worker capacity - threshold crossed" \
    "The weekly worker capacity watchdog crossed a threshold on $(ts).\n\n${BREACH}Signals: slow_starts_7d=$SLOW, pending_now=$PENDING, llm_ratelimit_7d=$RATELIMIT.\nRun the queries in scripts/worker_capacity_check.sh to dig in."
else
  echo "$(ts) [worker-capacity] OK - no threshold crossed, staying silent."
fi
