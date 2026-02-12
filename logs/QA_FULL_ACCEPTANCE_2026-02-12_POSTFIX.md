# QA Full Acceptance (Post-Fix) — 2026-02-12

- Environment: `https://app.welliam.codes/` (UI) + `/agent/v1/*` (API)
- Build/Deploy: commit `5f25038`, GitHub Actions `ci` + `deploy-staging` success, K8s rollout OK
- Method: Playwright UI regression + API verification (real staging data, không dùng UI mock)

## 1) Smoke 9 tab

| Tab | Result |
|---|---|
| dashboard | PASS |
| ocr | PASS |
| journal | PASS |
| reconcile | PASS |
| risk | PASS |
| forecast | PASS |
| qna | PASS |
| reports | PASS |
| settings | PASS |

## 2) Flow verdict (PASS/PARTIAL/FAIL)

| Flow | Result | Evidence chính |
|---|---|---|
| OCR upload | PASS | `POST /agent/v1/attachments` = 200; vouchers `source=ocr_upload` tăng `0 -> 1` |
| Journal approve | PASS | `POST /agent/v1/acct/journal_proposals/{id}/review` = 200 |
| Reconcile auto/manual | PASS | Auto: `POST /agent/v1/runs` (`run_type=bank_reconcile`) = 200; Manual: `POST /agent/v1/acct/bank_match` = 200; `matched_manual` tăng `0 -> 1` |
| Risk resolve | PASS | Resolve anomaly open từ UI: `POST /agent/v1/acct/anomaly_flags/{open_id}/resolve` = 200 |
| Forecast | PASS | UI load `GET /agent/v1/acct/cashflow_forecast` = 200; run forecast `POST /agent/v1/runs` (`cashflow_forecast`) = 200 |
| Q&A + feedback | PASS | API 3 câu hỏi: `POST /acct/qna` = 200, `meta.llm_used=true`, không có `reasoning_chain`; UI feedback `PATCH /acct/qna_feedback/{id}` = 200 |
| Reports | PASS | API: validate/preview/generate đều 200; UI preview 200, validate 200, quick export generate 200 |
| Feeder control | PASS | UI Start/Inject/Stop: `POST /vn_feeder/control` = 200; `GET /vn_feeder/status` phản ánh running/events |

## 3) Bug fix evidence (theo yêu cầu vòng này)

### P0.1 OCR `/agent/v1/attachments` 500
- Fixed: backend hỗ trợ multipart upload binary (PDF/XML/JPG/JPEG/PNG), không decode binary bừa bãi.
- Result: upload file Kaggle qua tab OCR trả 200, tạo attachment + voucher mirror (`source=ocr_upload`).

### P0.2 Reports `/reports/validate|preview|generate` 500
- Fixed:
  - FE chặn gọi khi thiếu `type`/`period`.
  - BE validate input `type/period` (400 rõ ràng nếu sai), sửa aggregation journal dùng field đúng (`debit/credit`), generate snapshot có `id` hợp lệ.
- Result:
  - `GET /reports/validate?type=balance_sheet&period=2026-02` = 200
  - `POST /reports/preview` = 200
  - `POST /reports/generate` = 200

### P1.1 Q&A feedback 422
- Fixed:
  - FE map `👍/👎` -> `feedback: helpful/not_helpful`.
  - BE backward-compatible nhận legacy `rating` (`1/-1`).
- Result: UI feedback PATCH = 200, không còn 422.

### P1.2 Reconcile chưa persist backend
- Fixed:
  - FE Auto-match gọi `/runs` với `run_type=bank_reconcile` + `period`.
  - FE Manual match gọi endpoint mới `/acct/bank_match`.
  - BE thêm endpoints persist: `/acct/bank_match`, `/acct/bank_match/{id}/unmatch`, `/acct/bank_transactions/{id}/ignore`.
- Result: mutation backend thành công (manual match 200, trạng thái DB đổi).

### P1.3 Settings Feeder chưa wired
- Fixed:
  - FE section Feeder dùng thật `/vn_feeder/status` + `/vn_feeder/control` (start/stop/inject/update_config).
  - BE thêm `update_config`, expose `events_per_min` trong status/control.
- Result: UI Start/Inject/Stop đều 200, status sync đúng.

## 4) Network/console quality gate
- Không ghi nhận HTTP 500 trong các flow test chính.
- Network sample cuối phiên: chỉ có `200` và 1 trường hợp `409` hợp lệ khi resolve anomaly đã xử lý trước đó.
- Không có console error nghiêm trọng ảnh hưởng flow chính.

## 5) Artifacts
- Detailed raw run (JSON): `/tmp/qa_regression_prod_result.json`
- Targeted confirmation JSON (Q&A/Reports/Risk UI): `/tmp/qa_targeted_checks.json`

## 6) Kết luận
- OCR + Reports blockers P0: **đã hết blocker**.
- Các flow chính theo checklist vòng này: **đã chạy được, không còn 500**.
