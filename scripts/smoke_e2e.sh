#!/usr/bin/env bash
# scripts/smoke_e2e.sh — quick end-to-end smoke test for OpenClaw Agent ERPX.
# Verifies that docker-compose services boot up, healthz/readyz respond,
# and at least 2 workflow run_types execute successfully.
#
# Usage:
#   ./scripts/smoke_e2e.sh          # default: http://localhost:8000
#   AGENT_BASE_URL=http://... ./scripts/smoke_e2e.sh
#
# Prereqs: docker compose services must be running.

set -euo pipefail

BASE="${AGENT_BASE_URL:-http://localhost:8000}"
API_KEY="${AGENT_API_KEY:-}"
PASS=0
FAIL=0

headers=""
if [[ -n "$API_KEY" ]]; then
    headers="-H X-API-Key:${API_KEY}"
fi

log_pass() { echo "[PASS] $*"; PASS=$((PASS+1)); }
log_fail() { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }

wait_healthy() {
    local url="$1" max_wait="${2:-30}" elapsed=0
    echo "[INFO] Waiting for $url ..."
    while [[ $elapsed -lt $max_wait ]]; do
        if curl -sf $headers "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed+1))
    done
    return 1
}

# --- 1. Healthz / Readyz ---
if wait_healthy "$BASE/healthz" 30; then log_pass "GET /healthz"; else log_fail "GET /healthz"; fi
if wait_healthy "$BASE/readyz" 30; then log_pass "GET /readyz"; else log_fail "GET /readyz"; fi
if wait_healthy "$BASE/agent/v1/healthz" 5; then log_pass "GET /agent/v1/healthz"; else log_fail "GET /agent/v1/healthz"; fi
if wait_healthy "$BASE/agent/v1/readyz" 5; then log_pass "GET /agent/v1/readyz"; else log_fail "GET /agent/v1/readyz"; fi

# --- 2. Trigger soft_checks run ---
echo "[INFO] Triggering soft_checks run..."
RESPONSE=$(curl -sf -X POST $headers \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: smoke-soft-checks-$(date +%s)" \
    -d '{"run_type":"soft_checks","trigger_type":"manual","payload":{"updated_after_hours":999}}' \
    "$BASE/agent/v1/runs" 2>&1 || echo "ERROR")

if echo "$RESPONSE" | grep -q "run_id"; then
    RUN_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null || echo "")
    log_pass "POST /agent/v1/runs (soft_checks) run_id=$RUN_ID"
else
    log_fail "POST /agent/v1/runs (soft_checks): $RESPONSE"
fi

# --- 3. Trigger close_checklist run ---
echo "[INFO] Triggering close_checklist run..."
RESPONSE2=$(curl -sf -X POST $headers \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: smoke-close-checklist-$(date +%s)" \
    -d '{"run_type":"close_checklist","trigger_type":"manual","payload":{"period":"2026-01"}}' \
    "$BASE/agent/v1/runs" 2>&1 || echo "ERROR")

if echo "$RESPONSE2" | grep -q "run_id"; then
    RUN_ID2=$(echo "$RESPONSE2" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])" 2>/dev/null || echo "")
    log_pass "POST /agent/v1/runs (close_checklist) run_id=$RUN_ID2"
else
    log_fail "POST /agent/v1/runs (close_checklist): $RESPONSE2"
fi

# --- 4. List runs ---
echo "[INFO] Listing runs..."
RUNS=$(curl -sf $headers "$BASE/agent/v1/runs?limit=10" 2>&1 || echo "ERROR")
if echo "$RUNS" | grep -q "items"; then
    COUNT=$(echo "$RUNS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['items']))" 2>/dev/null || echo "0")
    log_pass "GET /agent/v1/runs — $COUNT runs listed"
else
    log_fail "GET /agent/v1/runs: $RUNS"
fi

# --- 5. Wait for worker (poll run status) ---
if [[ -n "${RUN_ID:-}" ]]; then
    echo "[INFO] Waiting up to 60s for run $RUN_ID to complete..."
    for i in $(seq 1 60); do
        STATUS=$(curl -sf $headers "$BASE/agent/v1/runs/$RUN_ID" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "unknown")
        if [[ "$STATUS" == "success" ]]; then
            log_pass "Run $RUN_ID completed: success"
            break
        elif [[ "$STATUS" == "failed" ]]; then
            log_fail "Run $RUN_ID completed: failed"
            break
        fi
        sleep 1
    done
    if [[ "$STATUS" != "success" && "$STATUS" != "failed" ]]; then
        log_fail "Run $RUN_ID did not complete within 60s (status=$STATUS)"
    fi

    # List tasks + logs
    TASKS=$(curl -sf $headers "$BASE/agent/v1/tasks?run_id=$RUN_ID" 2>&1 || echo "{}")
    LOGS=$(curl -sf $headers "$BASE/agent/v1/logs?run_id=$RUN_ID&limit=50" 2>&1 || echo "{}")
    T_COUNT=$(echo "$TASKS" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('items',[])))" 2>/dev/null || echo "0")
    L_COUNT=$(echo "$LOGS" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('items',[])))" 2>/dev/null || echo "0")
    echo "[INFO] Tasks: $T_COUNT, Logs: $L_COUNT"
fi

echo ""
echo "========================================="
echo "  SMOKE E2E SUMMARY: PASS=$PASS FAIL=$FAIL"
echo "========================================="

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
