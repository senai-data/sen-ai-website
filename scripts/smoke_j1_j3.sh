#!/bin/bash
# =============================================================================
# sen-ai.fr Phase 1 smoke test (J1 + J2 + J3 validation)
# =============================================================================
# Exercises the new scan-as-brand endpoints against the live API.
#
# Covers:
#   - GET    /api/scans                         (list with new fields)
#   - GET    /api/scans/:id                     (detail with name/focus/parent/run_index)
#   - GET    /api/scans/:id/brands              (per-scan bucket structure)
#   - GET    /api/scans/:id/lineage             (root + runs)
#   - PATCH  /api/scans/:id                     (reversible name edit)
#   - POST   /api/scans/:id/brands/validate     (rejects wrong status = 400)
#
# Does NOT cover (to avoid polluting prod data):
#   - POST   /api/scans/:id/brands/classify     (use manual UI test B1+B2)
#   - POST   /api/scans/:id/rescan              (would trigger HaloScan + LLM jobs)
#
# Usage:
#   SENAI_EMAIL=data@sen-ai.fr SENAI_PASSWORD='…' ./scripts/smoke_j1_j3.sh
#
# Dependencies: curl, python3 (NO jq needed — pyjson helper below)
# =============================================================================

set -eu

API="${SENAI_API:-https://sen-ai.fr/api}"
EMAIL="${SENAI_EMAIL:-}"
PASSWORD="${SENAI_PASSWORD:-}"

# Detect python binary (Linux = python3, Windows Git Bash = python)
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "ERROR: neither python3 nor python found in PATH" >&2
  exit 2
fi

# Known scan IDs accessible to the admin user (Pierre Fabre client)
# Note: renefurterer (684b7acc...) is on a different client → not accessible here.
# For a full test including focus-is-set assertions, log in as the renefurterer client owner.
DUCRAY_ID="260a109a-be08-4dc8-aec7-0a9c6f0e4a8d"
LRP_ID="b0ea6068-9aa2-4121-a88c-3cf95ba08f10"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
note() { echo -e "  ${BLUE}·${NC} $1"; }
section() { echo -e "\n${YELLOW}── $1 ──${NC}"; }

# --- JSON helpers (python-based, no jq dep) ---
# pyjson <path>  : read stdin JSON, print value at dotted path (e.g. ".a.b[0].id")
#                  returns empty string if missing/null
pyjson() {
  $PY -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception as e:
    print('', end=''); sys.exit(0)
path = sys.argv[1].lstrip('.')
cur = data
for part in [p for p in path.replace('[', '.[').split('.') if p]:
    if part.startswith('['):
        try:
            cur = cur[int(part[1:-1])]
        except Exception:
            cur = None; break
    else:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None; break
if cur is None:
    print('', end='')
elif isinstance(cur, bool):
    print('true' if cur else 'false', end='')
else:
    print(cur, end='')
" "$1"
}

# pylen <path>  : print length of array at path
pylen() {
  $PY -c "
import sys, json
data = json.load(sys.stdin)
path = sys.argv[1].lstrip('.')
cur = data
for part in [p for p in path.split('.') if p]:
    cur = cur.get(part) if isinstance(cur, dict) else None
    if cur is None: break
print(len(cur) if isinstance(cur, list) else 0, end='')
" "$1"
}

# pyhas <field> : check top-level field presence (returns 'true'/'false')
pyhas() {
  $PY -c "
import sys, json
data = json.load(sys.stdin)
print('true' if isinstance(data, dict) and '$1' in data else 'false', end='')
"
}

# pyfilter_count <filter>  : count items in top-level array matching filter
# Example: pyfilter_count '.is_focus == True'  → count of is_focus=true items
# filter is python expression against variable `x`
pyfilter_count() {
  $PY -c "
import sys, json
data = json.load(sys.stdin)
arr = data if isinstance(data, list) else data.get('$2', []) if '$2' else []
count = sum(1 for x in arr if eval('$1'))
print(count, end='')
"
}

assert_eq() {
  local actual="$1" expected="$2" label="$3"
  if [ "$actual" = "$expected" ]; then
    pass "$label ($actual)"
  else
    fail "$label — expected=$expected actual=$actual"
  fi
}

assert_not_empty() {
  local val="$1" label="$2"
  if [ -n "$val" ]; then
    pass "$label = $val"
  else
    fail "$label is empty/null"
  fi
}

assert_empty() {
  local val="$1" label="$2"
  if [ -z "$val" ]; then
    pass "$label is empty (as expected)"
  else
    fail "$label expected empty, got '$val'"
  fi
}

# =============================================================================
# 0. Credentials check
# =============================================================================
if [ -z "$EMAIL" ] || [ -z "$PASSWORD" ]; then
  echo -e "${RED}ERROR${NC}: set SENAI_EMAIL and SENAI_PASSWORD env vars first."
  echo "  Example:"
  echo "    SENAI_EMAIL=data@sen-ai.fr SENAI_PASSWORD='…' ./scripts/smoke_j1_j3.sh"
  exit 2
fi

# =============================================================================
# 1. Login
# =============================================================================
section "1. Login"
LOGIN_RESP=$(curl -skL -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")

TOKEN=$(echo "$LOGIN_RESP" | pyjson ".access_token")
if [ -z "$TOKEN" ]; then
  fail "login failed — response: $LOGIN_RESP"
  exit 1
fi
pass "got JWT token (${TOKEN:0:24}…)"
AUTH_HEADER="Cookie: token=$TOKEN"

api_get() {
  curl -skL -H "$AUTH_HEADER" "$API$1"
}

# =============================================================================
# 2. GET /api/scans — list with new fields
# =============================================================================
section "2. GET /api/scans (list)"
CLIENT_ID=$(api_get "/clients/" | pyjson ".[0].id")
assert_not_empty "$CLIENT_ID" "resolved client_id"

LIST_JSON=$(api_get "/scans?client_id=$CLIENT_ID")
COUNT=$(echo "$LIST_JSON" | $PY -c "import sys,json; print(len(json.load(sys.stdin)), end='')")
note "total scans: $COUNT"

# Verify first scan has all new fields
for field in id name domain status focus_brand_id parent_scan_id run_index schedule; do
  val=$(echo "$LIST_JSON" | $PY -c "
import sys,json
d = json.load(sys.stdin)
print('true' if d and isinstance(d[0], dict) and '$field' in d[0] else 'false', end='')
")
  if [ "$val" = "true" ]; then
    pass "list item exposes field: $field"
  else
    fail "list item missing field: $field"
  fi
done

# =============================================================================
# 3. GET /api/scans/:id — detail view for the 3 backfilled scans
# =============================================================================
section "3. GET /api/scans/:id (detail x2)"

for id_name in "DUCRAY_ID:ducray.com/fr-fr" "LRP_ID:www.laroche-posay.fr"; do
  var_name="${id_name%%:*}"
  expected_domain="${id_name#*:}"
  scan_id=$(eval echo "\$$var_name")
  note "scan $scan_id"
  RESP=$(api_get "/scans/$scan_id")

  assert_eq "$(echo "$RESP" | pyjson ".id")" "$scan_id" "  .id"
  assert_eq "$(echo "$RESP" | pyjson ".domain")" "$expected_domain" "  .domain"
  assert_eq "$(echo "$RESP" | pyjson ".run_index")" "1" "  .run_index"
  assert_eq "$(echo "$RESP" | pyjson ".schedule")" "manual" "  .schedule"
  assert_eq "$(echo "$RESP" | pyjson ".name")" "$expected_domain" "  .name (== domain after backfill)"
done

# =============================================================================
# 4. GET /api/scans/:id/brands — bucket structure + focus expectations
# =============================================================================
section "4. GET /api/scans/:id/brands (buckets + focus invariant)"

# Helper: for a given brands response, check bucket structure AND focus invariant:
#   - all 4 buckets present as arrays
#   - if .focus_brand_id is set → exactly 1 my_brand has is_focus=true AND its brand_id matches
#   - if .focus_brand_id is null → 0 my_brand items have is_focus=true
check_scan_brands() {
  local scan_label="$1"
  local resp="$2"

  for bucket in my_brand competitor ignored unclassified; do
    len=$(echo "$resp" | $PY -c "
import sys,json
d = json.load(sys.stdin)
b = d.get('buckets', {}).get('$bucket')
print(len(b) if isinstance(b, list) else -1, end='')
")
    if [ "$len" -ge 0 ]; then
      pass "  $scan_label.buckets.$bucket is array ($len items)"
    else
      fail "  $scan_label.buckets.$bucket missing or wrong type"
    fi
  done

  # Focus invariant
  RESULT=$(echo "$resp" | $PY -c "
import sys,json
d = json.load(sys.stdin)
fid = d.get('focus_brand_id')
focus_rows = [x for x in d.get('buckets',{}).get('my_brand',[]) if x.get('is_focus')]
n = len(focus_rows)
if fid:
    if n == 1 and focus_rows[0].get('brand_id') == fid:
        print('OK_SET:' + fid[:8])
    elif n == 0:
        print('FAIL:focus_brand_id set but no is_focus row')
    elif n > 1:
        print('FAIL:' + str(n) + ' is_focus rows (expected 1)')
    else:
        print('FAIL:is_focus row does not match focus_brand_id')
else:
    if n == 0:
        print('OK_NULL')
    else:
        print('FAIL:focus_brand_id null but ' + str(n) + ' is_focus rows')
")
  case "$RESULT" in
    OK_SET:*)   pass "  $scan_label focus invariant holds (focus=${RESULT#OK_SET:}…)" ;;
    OK_NULL)    pass "  $scan_label focus invariant holds (no focus set)" ;;
    FAIL:*)     fail "  $scan_label focus invariant broken — ${RESULT#FAIL:}" ;;
    *)          fail "  $scan_label focus invariant check returned unexpected: $RESULT" ;;
  esac
}

note "ducray"
R2=$(api_get "/scans/$DUCRAY_ID/brands")
check_scan_brands "ducray" "$R2"

note "laroche-posay"
R3=$(api_get "/scans/$LRP_ID/brands")
check_scan_brands "lrp" "$R3"

# =============================================================================
# 5. GET /api/scans/:id/lineage
# =============================================================================
section "5. GET /api/scans/:id/lineage"
LIN=$(api_get "/scans/$DUCRAY_ID/lineage")
assert_eq "$(echo "$LIN" | pyjson ".root_scan_id")" "$DUCRAY_ID" "  .root_scan_id"
RUNS_LEN=$(echo "$LIN" | $PY -c "
import sys,json
d = json.load(sys.stdin)
print(len(d.get('runs', [])), end='')
")
assert_eq "$RUNS_LEN" "1" "  .runs length (no rescans yet)"
assert_eq "$(echo "$LIN" | pyjson ".runs[0].run_index")" "1" "  .runs[0].run_index"

# =============================================================================
# 6. PATCH /api/scans/:id — reversible name edit
# =============================================================================
section "6. PATCH /api/scans/:id (reversible name edit)"
ORIG_NAME=$(api_get "/scans/$DUCRAY_ID" | pyjson ".name")
note "original name: $ORIG_NAME"

TEST_NAME="smoke-test-$(date +%s)"
PATCH_RESP=$(curl -skL -X PATCH "$API/scans/$DUCRAY_ID" \
  -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "{\"name\":\"$TEST_NAME\"}")
NEW_NAME=$(echo "$PATCH_RESP" | pyjson ".name")
assert_eq "$NEW_NAME" "$TEST_NAME" "  name updated via PATCH"

# Restore
curl -skL -X PATCH "$API/scans/$DUCRAY_ID" \
  -H "$AUTH_HEADER" -H "Content-Type: application/json" \
  -d "{\"name\":\"$ORIG_NAME\"}" > /dev/null
RESTORED=$(api_get "/scans/$DUCRAY_ID" | pyjson ".name")
assert_eq "$RESTORED" "$ORIG_NAME" "  name restored to original"

# =============================================================================
# 7. POST /api/scans/:id/brands/validate — should reject wrong status (400)
# =============================================================================
section "7. POST /api/scans/:id/brands/validate (expect 400 on completed scan)"
HTTP_CODE=$(curl -skL -o /tmp/validate_resp.json -w "%{http_code}" \
  -X POST "$API/scans/$DUCRAY_ID/brands/validate" \
  -H "$AUTH_HEADER")
assert_eq "$HTTP_CODE" "400" "  HTTP status on wrong-state scan"
DETAIL=$(cat /tmp/validate_resp.json | pyjson ".detail")
note "rejection message: $DETAIL"
if echo "$DETAIL" | grep -qi "brands_ready"; then
  pass "  error message mentions 'brands_ready'"
else
  fail "  error message doesn't mention 'brands_ready' (got: $DETAIL)"
fi
rm -f /tmp/validate_resp.json

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${YELLOW}============================================${NC}"
echo -e "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
echo -e "${YELLOW}============================================${NC}"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
