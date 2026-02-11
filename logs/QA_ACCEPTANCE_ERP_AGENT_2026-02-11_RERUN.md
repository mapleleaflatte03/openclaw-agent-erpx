# QA Acceptance Rerun — ERP-X AI Ke toan Agent (2026-02-11)

## Scope
- Mode A (UI): `https://app.welliam.codes/`
- Mode B (API): `http://127.0.0.1:30080` with `X-API-Key` from secured runtime
- Constraint: blackbox, no code/config changes

## Regression
- `ruff check .` => PASS
- `pytest tests/ -q` => PASS (`107 passed, 5 skipped`)
- `python3 scripts/export_openapi.py` => PASS
- Health endpoints: `/healthz`, `/readyz`, `/agent/v1/healthz`, `/agent/v1/readyz` => HTTP 200
- UI tab sweep (dashboard, vouchers, journal, anomaly, cashflow, reports, contracts, qna, command center, create task) => no visible traceback/500 in test session

## PO Criteria Verdict

### (1) Q&A ke toan VN
**PARTIAL**
- API always reports `meta.llm_used=true` and no `reasoning_chain` field.
- Quality is unstable across repeated calls:
  - Some responses are detailed (TK references + VND + TT200/TT133)
  - Some responses are fallback/generic (`Xin loi... can them thong tin` / template lookup)
- Not reliable enough yet for strict acceptance on all 3 benchmark questions.

Evidence:
- UI screenshots:
  - `output/playwright/qa-accept-20260211/13-qna-131-331-ui.png`
  - `output/playwright/qa-accept-20260211/14-qna-642-641-ui.png`
  - `output/playwright/qa-accept-20260211/15-qna-khauhao-ui.png`

### (2) Chuoi ERP mo phong
**PARTIAL**
- Vouchers, journal proposals, anomaly flags, cashflow, soft-check/report data are present and load correctly.
- End-to-end causality from a newly ingested feeder event to full downstream chain is not consistently observable within the short acceptance window.

Evidence:
- API samples (all non-empty):
  - `/agent/v1/acct/vouchers?limit=5`
  - `/agent/v1/acct/journal_proposals?limit=5`
  - `/agent/v1/acct/anomaly_flags?limit=5`
  - `/agent/v1/acct/cashflow_forecast?limit=5`
- UI screenshots:
  - `output/playwright/qa-accept-20260211/02-tab-vouchers.png`
  - `output/playwright/qa-accept-20260211/03-tab-journal.png`
  - `output/playwright/qa-accept-20260211/04-tab-anomaly.png`
  - `output/playwright/qa-accept-20260211/05-tab-cashflow.png`
  - `output/playwright/qa-accept-20260211/06-tab-check-report.png`

### (3) VN Feeder + Command Center
**PASS**
- Feeder control works (`start`, `inject_now`, `stop`) and status transitions are correct.
- Metrics increase over time; source counters update for 3 data sources.
- UI Command Center shows state/metrics and controls.

Evidence:
- API status progression: `running false -> true -> false`, `total_events_today` increased during run.
- UI screenshots:
  - `output/playwright/qa-accept-20260211/10-tab-command-center.png`
  - `output/playwright/qa-accept-20260211/16-command-center-after-api-start-stop.png`

### (4) UI Tao tac vu + period
**PASS**
- Period field `YYYY-MM` is visible for `voucher_ingest`, `soft_checks`, `tax_export`.
- Backend validation is correct:
  - missing/invalid period => HTTP 422 (Vietnamese message)
  - valid period => run creation success

Evidence:
- UI screenshots:
  - `output/playwright/qa-accept-20260211/23-ui-run-voucher-confirm.png`
  - `output/playwright/qa-accept-20260211/24-ui-run-softchecks-confirm.png`
  - `output/playwright/qa-accept-20260211/25-ui-run-taxexport-confirm.png`
- API validation checks passed for missing/invalid/valid period cases.

## Security/Diagnostics check
- `/diagnostics/llm` returns `status: ok`
- `do_agent.base_url_masked = "configured"`
- No raw `base_url` field observed in diagnostics response

## Top issues before broader rollout
1. Q&A answer consistency: same question can oscillate between detailed answer and fallback.
2. ERP chain observability: per-event trace from ingest to downstream artifacts should be easier to verify in UI/API.
3. Manual task UX: run confirmation/status feedback in create-task flow should be clearer and less idempotency-confusing.
