# QA Regression ERP Agent — 2026-02-12 (UTC)

## 1) Scope & Environment
- URL UI: `https://app.welliam.codes/`
- API prefix: `https://app.welliam.codes/agent/v1`
- Repo commit deployed: `2019efd`
- QA method: API regression + Playwright UI smoke/flow (headless chromium `--no-sandbox`)
- Raw machine-readable evidence: `logs/qa_regression_erp_agent_20260212_raw.json`
- Screenshots: `output/playwright/qa-regression-20260212/`

## 2) CI/CD & Rollout
- `ci` workflow: **success** (run `21939975983`)
- `deploy-staging` workflow: **success** (run `21939975971`)
- K8s rollout:
  - `kubectl -n accounting-agent-staging rollout status deployment/agent-service` => success
  - Running image: `ghcr.io/mapleleaflatte03/accounting-agent-layer/agent-service:2019efda94cd23eb7ebab16b03362f9458590408`

## 3) Overall Result
- PASS: **27**
- PARTIAL: **1**
- FAIL: **0**

## 4) 9-Tab Matrix (UI)
| Tab | Status | Notes |
|---|---|---|
| dashboard | PASS | Render OK, command actions mapped to real runs |
| ocr | PASS | Upload UI + API OK, logs preview OK, reprocess run OK |
| journal | PASS | Render OK |
| reconcile | PASS | Render OK, auto/manual flows validated via API |
| risk | PARTIAL | Render OK; no open anomaly at test time so cannot re-verify resolve-by-UI mutation |
| forecast | PASS | Render OK, forecast run path validated |
| qna | PASS | Ask/answer/feedback OK, no reasoning-chain leak |
| reports | PASS | Wizard export button triggers `POST /reports/generate` (HTTP 200) |
| settings | PASS | Feeder controls wired and working |

## 5) Bug Checklist vs Target

### P0-1 Run engine queued-stuck
- Status: **Fixed**
- Evidence:
  - `GET /agent/v1/ray/status` => HTTP 200, dispatcher ready.
  - Reused stale idempotent runs were re-dispatched successfully:
    - `bank_reconcile` run `ac2bb8ce-a67f-4971-930a-013e7d349b0c` => `success`
    - `cashflow_forecast` run `7b6e6b0e-914b-4fab-80ac-3f5f62388c66` => `success`
    - `soft_checks` run `f8df8092-4f05-4a54-b35e-69c63f0415ef` => `success`
- Root cause found and fixed:
  1. Dispatch race (task consumed before DB commit) causing historical queued runs.
  2. Idempotency returned stale queued runs forever.
- Fix applied:
  - Commit-before-dispatch in `/runs`.
  - Executor readiness + dispatch info.
  - Stale pending run detection + re-dispatch for idempotent reuse.

### P0-2 Reports export/download
- Status: **Fixed (ERP-minimum artifact)**
- Evidence:
  - `GET /reports/validate?type=balance_sheet&period=2026-02` => 200
  - `POST /reports/preview` => 200
  - `POST /reports/generate` => 200 (artifact generated)
  - `GET /reports/{id}/download?format=json` => 200, `content-type: application/json`
  - UI step-4 `#btn-export-final` => generated request observed: HTTP 200
- Root cause found and fixed:
  1. UI button wiring missing/weak at final step.
  2. Backend returned placeholder JSON message instead of artifact flow.
  3. PDF-only default could fail when PDF renderer missing.
- Fix applied:
  - Final export button wired and verified.
  - Real artifact path generation + binary download endpoint.
  - PDF request now gracefully fallback to XLSX if renderer unavailable (no fake success, no placeholder).

### P1 OCR (logs + reprocess)
- Status: **Fixed**
- Evidence:
  - Upload: `POST /attachments` => 200; voucher count `source=ocr_upload` increased `9 -> 10`
  - Logs preview: `GET /logs?filter_entity_id=<voucher_id>&limit=20` => 200
  - Reprocess: `POST /runs` with `run_type=voucher_reprocess` => terminal `success`

### P1 Dashboard command no_chain
- Status: **Fixed**
- Evidence:
  - `POST /agent/commands` with `trigger_voucher_ingest` / `run_goal+close_period` now mapped to executable chains (no fake success path).

### P1 Reconcile persist
- Status: **Fixed**
- Evidence:
  - Auto run reaches terminal status (not stuck queued).
  - Manual match/unmatch:
    - `POST /acct/bank_match` => 200
    - `POST /acct/bank_match/{id}/unmatch` => 200

### P1 Risk resolve conflict
- Status: **Partially validated**
- Evidence:
  - Endpoint/status contract normalized (`status`/`resolution` mapping in API).
  - In this run there were no `open` anomalies to execute live resolve mutation.
- Residual note:
  - Need one targeted seed/anomaly scenario to re-assert UI resolve button behavior end-to-end.

### P1 Q&A metadata/UI
- Status: **Fixed (schema alignment)**
- Evidence:
  - 3 core questions all return 200 with answer payload.
  - `PATCH /acct/qna_feedback/{id}` => 200.
  - UI no longer hard-forces fake `0 tokens / 0%` from wrong fields.

### P2 Feeder
- Status: **Fixed/Working with expected eventual consistency**
- Evidence:
  - `POST /vn_feeder/control` start/inject/stop all 200.
  - Status refresh logic and note about delayed counters present.

## 6) Key Endpoint Evidence (sample)
- `POST /agent/v1/runs`
  - Payload: `{"run_type":"bank_reconcile","trigger_type":"manual","payload":{"period":"2026-02"}}`
  - Actual: 200, run transitions to `success`.
- `POST /agent/v1/reports/generate`
  - Payload: `{"type":"balance_sheet","standard":"VAS","period":"2026-02","format":"json"}`
  - Actual: 200, returns `report_id`, downloadable artifact.
- `POST /agent/v1/attachments`
  - Multipart: `file=<jpg>, source_tag=ocr_upload`
  - Actual: 200, voucher mirrored.
- `POST /agent/v1/agent/commands`
  - Payload: `{"command":"run_goal","goal":"close_period","period":"2026-02"}`
  - Actual: chain dispatches; no `no_chain` fake-200 path.

## 7) Remaining Issues / Recommendations (Neutral)
1. **Risk tab testability gap** (Severity: Medium)
   - No `open` anomaly existed at runtime, so resolve action couldn’t be re-validated from UI click path in this run.
   - Recommendation: add deterministic QA seed endpoint/fixture for one `open` anomaly in staging.
2. **Run idempotency policy clarity** (Severity: Medium)
   - Re-dispatch stale queued runs is now implemented; still recommended to expose `redispatched=true` prominently in UI logs/timeline.
3. **Report format UX** (Severity: Low)
   - Backend fallback from PDF to XLSX works; UI should surface selected vs effective format in history row to avoid user confusion.

## 8) Final Verdict
- **No P0 remaining** in validated flows.
- Core ERP flows (OCR, Reports, Q&A feedback, Reconcile, Feeder, run engine) are operational with real backend persistence and executable orchestration.
- One **PARTIAL** item remains for Risk resolve due lack of open anomaly data during this specific run, not due endpoint failure.
