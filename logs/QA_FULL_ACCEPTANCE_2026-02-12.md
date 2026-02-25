# QA Full Acceptance Report — Accounting Agent Layer ERP AI Kế toán

**Date:** 2026-02-12  
**Commit:** `6606337` (HEAD → main)  
**Tag:** none  
**URL UI:** https://app.welliam.codes/ (Caddy → k3s NodePort 30851 → UI nginx)  
**URL API direct:** http://127.0.0.1:30080 (k3s NodePort → agent-service:8000)  
**API subdomain:** https://api.welliam.codes/ (Caddy → k3s NodePort 30080)  
**Backend:** FastAPI, 61 endpoints  
**Test method:** API probing with X-API-Key auth + UI fetch_webpage + k8s log inspection  
**API Key env var:** `AGENT_API_KEY` (stored in k8s Secret `agent-secrets`)  

---

## 1. Tổng quan build

| Item | Value |
|------|-------|
| Commit | `6606337` — Fix loading overlay always visible + cache-busting |
| Branch | `main` |
| CI/CD | ✅ ci: success, ✅ deploy-staging: success |
| Pods | 10 running (agent-service, agent-scheduler, agent-worker-standby, erpx-mock-api, minio, postgres, redis, ui, 2× init/migrate completed) |
| DB data | 33 vouchers, 30 journal proposals, 25 bank transactions, 13 soft check results, 7 anomaly flags, 50 QnA audits, 60 validation issues, 6 cashflow forecast items |

---

## 2. Bảng tổng hợp 9 Tab theo SPEC UI

| # | Tab | Status | Ghi chú |
|---|-----|--------|---------|
| 1 | **Dashboard** | **FAIL** | KPIs trả `—` do UI không thể gọi API (P0: routing + auth). Khi gọi API trực tiếp: 4 KPI cards, timeline 50 events, chart data đều có dữ liệu thật. |
| 2 | **OCR** | **FAIL** | Drag-drop zone render OK. Upload flow tạo `FormData` nhưng KHÔNG gửi file content — chỉ gửi JSON metadata (`filename`, `size`). Backend không nhận được file thực tế. |
| 3 | **Hạch toán (Journal)** | **FAIL** | Grid masonry render OK, accordion 3 cấp OK. Nhưng Approve/Reject gửi `{action, reviewer}` trong khi API yêu cầu `{status, reviewed_by}` → 422 mọi lần. |
| 4 | **Đối chiếu (Reconcile)** | **PARTIAL** | Bảng merged/split view OK, slider threshold OK. Nhưng matching 100% client-side (không gọi server reconcile API), kết quả không persist. Nút manual-match/unmatch/ignore không wired. |
| 5 | **Rủi ro (Risk)** | **FAIL** | 3 gauge + doughnut + bar chart render OK. Resolve anomaly gửi `{resolution: freeText, resolver}` nhưng API yêu cầu `{resolution: "resolved"\|"ignored", resolved_by}` → 422. Detail modal chỉ tab Overview (JSON dump), 3 tab còn lại (Evidence, AI Suggestion, History) chưa wired. |
| 6 | **Dự báo (Forecast)** | **PARTIAL** | Chart.js multi-line render OK. API chỉ trả `amount` (base), KHÔNG có `optimistic`/`pessimistic`. UI fabricates: `base * 1.15` / `base * 0.85` → dữ liệu giả. Date range + KPI selector + weight slider không ảnh hưởng API call. |
| 7 | **Q&A** | **PARTIAL** | Chat bubble render OK. LLM thực sự được gọi (`llm_used: true`), trả lời tiếng Việt, không lộ `reasoning_chain`. Nhưng: không persist chat history, không file attach, API response không có `meta` field trong QnA audits endpoint. |
| 8 | **Báo cáo (Reports)** | **FAIL** | Wizard 4 bước render OK. API `/reports/validate` và `/reports/preview` trả 500 (`AttributeError: AcctVoucher has no attribute 'period'`). `/reports/generate` tạo metadata row nhưng không tạo file thật (`file_uri=None`). |
| 9 | **Cài đặt (Settings)** | **PARTIAL** | 5-section nav render OK. GET `/settings` trả dữ liệu thật. PATCH endpoints trả 405 (Method Not Allowed). Feeder start/stop qua API hoạt động (`running: true/false`). Import settings button chưa wired. |

---

## 3. Chi tiết từng Luồng (Flow 1 → 7)

### Luồng 1: OCR Ingest → Journal → Risk → Forecast → Report → Dashboard

**Steps:**
1. **Vouchers** — `GET /acct/vouchers`: 33 items, sources = `{erpx, mock_vn_fixture}`, 2 unique run_ids. Sample: `PT-000001`, 505,000 VND, source=erpx. ✅ Dữ liệu thật từ DB.
2. **Journal Proposals** — `GET /acct/journal_proposals`: 30 items, 30/30 linked to known voucher IDs. Status: 23 pending, 4 rejected, 3 approved. Mỗi proposal có `lines[]` với debit/credit cân bằng (đã verify 3 proposals). ✅ Data integrity OK.
3. **Approve proposal** — `POST /acct/journal_proposals/{id}/review`:
   - UI gửi: `{action: "approve", note: "...", reviewer: "web-user"}`
   - API expect: `{status: "approved", reviewed_by: "..."}`
   - Kết quả: **422 Unprocessable Entity** ❌
   - Sử dụng schema đúng qua curl: approve thành công ✅
4. **Anomaly flags** — `GET /acct/anomaly_flags`: 7 items (5 open, 2 resolved). Type = `amount_mismatch`. ✅
5. **Cashflow forecast** — `GET /acct/cashflow_forecast`: 6 items, summary = `{total_inflow: 6,630,000, total_outflow: 0, net: 6,630,000}`. **KHÔNG có trường `optimistic`/`pessimistic`** trong response. ⚠️
6. **Report snapshot** — `GET /reports/history`: 0 items. ⚠️ Chưa có snapshot nào.
7. **Dashboard KPIs** — Qua API trực tiếp: vouchers=33, anomaly_flags(pending)=5, proposals(pending)=23, cashflow_forecast(30d)=summary OK. ✅ Nhưng **UI không nhận được** do routing P0.

**Cascade verification:**
- Approve 1 proposal → status đổi từ `pending` → `approved` ✅
- Anomaly flags không tự động thay đổi khi approve proposal (không có downstream trigger) ⚠️
- Cashflow forecast không tự cập nhật khi approve (batch process, không real-time) ⚠️

### Luồng 2: Ngân hàng/Thuế → Reconcile

**Steps:**
1. **Bank transactions** — `GET /acct/bank_transactions`: 25 items. Match status: `{anomaly: 7, matched: 18}`. Total: 22,260,500 VND. ✅
2. **Reconciliation** — UI tab Reconcile:
   - Fetches `bank_transactions?limit=500` + `vouchers?limit=500`
   - Matching algorithm: **100% client-side** (nested loop match by amount tolerance)
   - `runAutoMatch()` chỉ gọi lại `loadReconciliation()` (cùng logic)
   - **Không có server-side reconcile API**
   - Kết quả matching **không persist** về server
3. **Manual match/unmatch/ignore** — Nút render trên UI nhưng **không có event handler** ❌

**Kết luận:** Reconcile là UI-only demo. Không có data flow thật về backend.

### Luồng 3: Rủi ro (soft_checks)

**Steps:**
1. **Soft check results** — `GET /acct/soft_check_results`: 13 items, all period `2026-02`. Sample: passed=125/130, warnings=3, errors=2, score=0.9615. ✅
2. **Validation issues** — `GET /acct/validation_issues`: 60 items. Severity: `{warning: 36, error: 24}`. Resolution: `{open: 60}` (không có issue nào đã resolve). ✅
3. **Resolve anomaly** — `POST /acct/anomaly_flags/{id}/resolve`:
   - UI gửi: `{resolution: "user-typed-note", resolver: "web-user"}`
   - API expect: `{resolution: "resolved"|"ignored", resolved_by: string}`
   - Kết quả: **422** ❌
   - Khi gửi đúng schema: resolve thành công, trả về record đã cập nhật ✅
4. **Validation issue resolve** — `POST /acct/validation_issues/{id}/resolve`: Endpoint tồn tại, chưa test qua UI (UI dùng prompt thay vì form).

### Luồng 4: Dự báo đa kịch bản

**Steps:**
1. **Forecast data** — `GET /acct/cashflow_forecast?horizon_days=365`: 6 items, mỗi item chỉ có `amount` (base), `forecast_date`, `direction`, `confidence`. **KHÔNG có `optimistic` / `pessimistic`** trong API response.
2. **UI fabrication** — `forecast.js` line 220, 230, 281, 282:
   ```js
   d.optimistic ?? (d.base ?? 0) * 1.15  // fabricated
   d.pessimistic ?? (d.base ?? 0) * 0.85  // fabricated
   ```
   → Dữ liệu optimistic/pessimistic trên chart/table là **giả**, không từ server.
3. **Date range** — Input render trên UI nhưng **không gửi** qua API (API call luôn dùng `horizon_days=365` cố định).
4. **Seasonality weight slider** — Render OK nhưng **không có effect** lên forecast. ⚠️
5. **Export** — PNG (canvas.toDataURL), CSV/Excel (thực chất là CSV). ✅

### Luồng 5: Q&A + LLM

**Steps:**
1. **Câu 1: "TK 131 vs 331"** — ✅
   - `llm_used: true`, `reasoning_chain: false`
   - Trả lời tiếng Việt, có Nợ/Có, VND, dẫn chiếu đúng
   - Không generic/EN markers
2. **Câu 2: "642 vs 641"** — ✅
   - `llm_used: true`, `reasoning_chain: false`
   - Markdown format tốt (bold, bullet list)
   - Phân biệt rõ ràng chi phí bán hàng vs QLDN
3. **Câu 3: "Khấu hao TSCĐ 30M/3 năm"** — ✅
   - `llm_used: true`, `reasoning_chain: false`
   - Tính đúng: 10,000,000/năm → 833,333/tháng
   - Có bút toán Nợ 6274 / Có 2141

**Kiểm tra bổ sung:**
- QnA audits: 50 records cũ trong DB. Mỗi record có `id, question, answer, sources, user_id, feedback, created_at`. **Không có field `meta.llm_used`** trong audits endpoint (chỉ có trong live QnA response) ⚠️
- Feedback: `PATCH /acct/qna_feedback/{audit_id}` trả 404 khi audit_id không tồn tại (validation OK) ✅
- File attach: UI **không có nút attach file** — chỉ có textarea + send ❌

### Luồng 6: Báo cáo tài chính

**Steps:**
1. **Validate** — `GET /reports/validate?type=balance_sheet&period=2026-02`:
   - **500 Internal Server Error** ❌
   - Backend traceback: `AttributeError: type object 'AcctVoucher' has no attribute 'period'`
2. **Preview** — `POST /reports/preview`:
   - **500 Internal Server Error** ❌ (cùng lỗi)
3. **Generate** — `POST /reports/generate`:
   - **500 Internal Server Error** ❌ (cùng lỗi)
4. **History** — `GET /reports/history`: Trả 0 items (chưa có snapshot nào). ✅ (nhưng vô nghĩa vì generate fail)
5. **Download** — `GET /reports/{id}/download`: Chưa test (không có report_id). Theo code review, endpoint trả JSON message "PDF file not yet generated."

**Root cause:** Model `AcctVoucher` không có attribute `period`. Code tại `main.py:2340` sử dụng `AcctVoucher.period` nhưng model chỉ có `date`.

### Luồng 7: Agent/Feeder + Settings

**Steps:**
1. **Feeder status** — `GET /vn_feeder/status`: `{running: false, total_events_today: 0, last_event_at: "", sources: []}`. ✅
2. **Start feeder** — `POST /vn_feeder/control {action: "start"}`: `{status: "ok", action: "start"}`. ✅
3. **Verify running** — `GET /vn_feeder/status`: `running: true`. ✅
4. **Stop feeder** — `POST /vn_feeder/control {action: "stop"}`: `{status: "ok", action: "stop"}`. ✅
5. **Settings read** — `GET /settings`: Trả dữ liệu thật (`model: gpt-4o, temperature: 0.3, ...`). ✅
6. **Settings write** — `PATCH /settings/*`: Tất cả trả **405 Method Not Allowed**. ❌
7. **Agent timeline** — `GET /agent/timeline?limit=5`: 5 events gần nhất, có run_id, task_id, timestamps. ✅
8. **Feeder → Dashboard cascade** — Khi feeder start, events = 0 → không tăng sau 2s. Feeder không thực sự generate events trong thời gian ngắn. ⚠️

---

## 4. Đánh giá LLM & Agent

### LLM
| Tiêu chí | Kết quả |
|----------|---------|
| `llm_used=true` | ✅ Có trong live response |
| Không lộ `reasoning_chain` | ✅ Không có trong response JSON |
| Tiếng Việt | ✅ Trả lời hoàn toàn tiếng Việt |
| Có Nợ/Có, VND | ✅ Cả 3 câu đều có |
| Dẫn chiếu Thông tư | ✅ Có dẫn chiếu TK theo hệ thống VAS |
| Không generic/EN | ✅ Không có "I think", "Let me", "As an AI" |
| Chất lượng nội dung | ✅ Chính xác nghiệp vụ kế toán VN |

**Kết luận LLM:** ✅ PASS — LLM hoạt động tốt, trả lời chuyên nghiệp, không leak nội bộ.

### Agent/Feeder
| Tiêu chí | Kết quả |
|----------|---------|
| Start/Stop feeder | ✅ API hoạt động |
| Feeder tạo events | ⚠️ Không quan sát được events mới trong 2s (có thể cần thời gian lâu hơn) |
| Agent runs | ✅ 50 runs trong DB, có run_id, task_id |
| Agent timeline | ✅ 50 events với timestamps, types (run/task), status |
| Agent thực sự drive data | ✅ Vouchers có run_id, proposals linked to vouchers |
| Cascade effects | ⚠️ Approve proposal → không tự trigger downstream (risk, forecast, reconcile) |

**Kết luận Agent:** ⚠️ PARTIAL — Agent đã chạy và tạo dữ liệu thật. Nhưng downstream cascade (approve → auto-update risk/forecast/reconcile) não có — mỗi pipeline chạy independent.

---

## 5. Danh sách BUG / Chênh lệch

### P0 — Critical (Blocking)

| # | Bug | Tab | Endpoint/Data | Cách reproduce | 
|---|-----|-----|---------------|----------------|
| P0-1 | **Nginx proxy path routing mismatch** — UI nginx `proxy_pass http://agent-service:8000/` (trailing `/` strips `/agent/` prefix). Backend routes registered as `/agent/v1/*`. UI requests đến backend nhận **404 Not Found** cho MỌI endpoint. | ALL | nginx.conf `location /agent/` → backend logs show 404 for `/v1/*` | Mở https://app.welliam.codes/, DevTools → Network → mọi API call trả 404 |
| P0-2 | **UI không gửi X-API-Key** — Backend k8s có `AGENT_AUTH_MODE=api_key` nhưng UI nginx không inject header. Kể cả fix P0-1, UI vẫn nhận **401 Unauthorized**. | ALL | configmap.yaml `AGENT_AUTH_MODE: "api_key"`, app.js `api()` có `Content-Type` nhưng không có `X-API-Key` | Gọi API không có header → 401 |
| P0-3 | **Journal review schema mismatch** — UI gửi `{action: "approve", note, reviewer}`, API yêu cầu `{status: "approved", reviewed_by}` → **422 Validation Error** mọi lần approve/reject. | Journal | `POST /acct/journal_proposals/{id}/review` | Click Approve/Reject trên bất kỳ proposal nào |
| P0-4 | **Risk resolve schema mismatch** — UI gửi `{resolution: freeText, resolver}`, API yêu cầu `{resolution: "resolved"\|"ignored", resolved_by}` → **422**. | Risk | `POST /acct/anomaly_flags/{id}/resolve` | Click resolve trên bất kỳ anomaly nào |
| P0-5 | **Report endpoints 500** — `AcctVoucher` model không có attribute `period`. Cả 3 endpoints (validate, preview, generate) đều crash. | Reports | `GET /reports/validate`, `POST /reports/preview`, `POST /reports/generate` | Chọn bất kỳ loại báo cáo + kỳ → 500 |
| P0-6 | **OCR upload không gửi file content** — UI tạo `FormData` nhưng gửi JSON metadata thay vì multipart. Backend không nhận được file thực tế. | OCR | `POST /agent/v1/runs` (hardcoded URL, not using `api()` helper) | Kéo thả file vào vùng upload |

### P1 — High

| # | Bug | Tab | Endpoint/Data | Cách reproduce |
|---|-----|-----|---------------|----------------|
| P1-1 | **Forecast data fabrication** — UI tạo `optimistic = base * 1.15`, `pessimistic = base * 0.85` khi API không trả trường này. Dữ liệu hiển thị trên chart/table là giả. | Forecast | `GET /acct/cashflow_forecast` — response không có `optimistic`/`pessimistic` | Bật kịch bản optimistic/pessimistic, so sánh với API response |
| P1-2 | **Reconcile 100% client-side** — Matching logic chạy trên browser, không gọi server API, kết quả không persist. Nút manual match/unmatch/ignore có render nhưng không có handler. | Reconcile | Không có reconcile endpoint | Click "Auto-match" → chỉ reload cùng data |
| P1-3 | **Settings PATCH 405** — Tất cả `PATCH /settings/*` trả 405 Method Not Allowed. User không thể save thay đổi settings. | Settings | `PATCH /settings/profile`, `/settings/agent`, etc. | Thay đổi bất kỳ setting → Save → 405 |
| P1-4 | **Journal fallback mock strings** — Khi API trả null: `confidence ?? 0.85`, `rules_matched \|\| ['TT133 §12.3']`, `ref_article \|\| '12'`, `llm_reasoning \|\| 'Dựa trên...'`. Hiển thị dữ liệu giả cho user. | Journal | journal.js L115, L152, L159, L166 | Proposal có null fields sẽ hiển thị hardcoded strings |
| P1-5 | **Report generate tạo metadata nhưng không tạo file** — `POST /reports/generate` tạo row `AcctReportSnapshot` với `file_uri=None`. Download trả "PDF file not yet generated." | Reports | `POST /reports/generate`, `GET /reports/{id}/download` | Generate bất kỳ report → download → lỗi |

### P2 — Medium

| # | Bug | Tab | Endpoint/Data | Cách reproduce |
|---|-----|-----|---------------|----------------|
| P2-1 | **Forecast date range/KPI selector ignored** — Render UI nhưng giá trị không gửi qua API. API luôn gọi `horizon_days=365`. | Forecast | `GET /acct/cashflow_forecast?horizon_days=365` | Thay đổi date range, observe Network tab |
| P2-2 | **Risk detail modal — chỉ tab Overview** — Tab Evidence, AI Suggestion, History render nhưng chưa wired. Chỉ tab Overview hiển thị JSON dump. | Risk | risk.js detail modal | Mở detail modal → click các tab khác → trống |
| P2-3 | **Reports imports non-existent functions** — `showModal`/`hideModal` destructured từ `window.ERPX` nhưng ERPX export `openModal`/`closeModal` → ReferenceError. | Reports | reports.js L4 | Mở tab Reports trên browser |
| P2-4 | **QnA audits thiếu `meta` field** — `GET /acct/qna_audits` trả items không có `meta.llm_used`. Chỉ live QnA response có `meta`. | Q&A | `GET /acct/qna_audits` | So sánh audit response vs live QnA response |
| P2-5 | **Voucher source = `mock_vn_fixture`** — Một phần vouchers có `source: "mock_vn_fixture"`, cho thấy dữ liệu được seed từ fixture, không phải Kaggle thật. | Dashboard, OCR | `GET /acct/vouchers` → `source` field | Filter vouchers by source |
| P2-6 | **Soft check results trùng lặp** — 13 records cùng period `2026-02`, cùng `passed=125, warnings=3, errors=2, score=0.9615`. Dữ liệu duplicated. | Risk | `GET /acct/soft_check_results` | So sánh múi records |
| P2-7 | **Settings theme conflict** — `settings.js` set `body.classList` toggle theme, nhưng `app.js` dùng `data-theme` attribute. Hai cơ chế xung đột. | Settings | app.js + settings.js | Toggle theme từ Settings vs topnav |
| P2-8 | **OCR upload dùng hardcoded URL** — `ocr.js` L143 gọi `fetch('/agent/v1/runs')` trực tiếp thay vì dùng `api()` helper → bỏ qua timeout, error handling, và potential auth headers. | OCR | ocr.js L143 | Upload file |
| P2-9 | **Export "Excel" thực chất là CSV** — Forecast export Excel button tạo file CSV, không phải `.xlsx`. | Forecast | forecast.js export | Click "Excel" export button |
| P2-10 | **CORS header thiếu X-API-Key** — `api.welliam.codes` Caddy config cho `Access-Control-Allow-Headers: "Authorization, Content-Type"` nhưng không bao gồm `X-API-Key`. | Infra | Caddyfile api.welliam.codes | Gọi API từ JavaScript cross-origin |
| P2-11 | **Bank transactions `matched_voucher_id` reference mismatch** — Bank tx có `matched_voucher_id: "VCH-0030"` nhưng vouchers dùng UUID format `b33b7d7d-...`, không phải `VCH-XXXX`. | Reconcile | `GET /acct/bank_transactions` | So sánh `matched_voucher_id` với voucher IDs |

---

## 6. Tổng kết

### Trạng thái hệ thống

| Layer | Status |
|-------|--------|
| **Backend API** | ⚠️ 55/61 endpoints hoạt động. Report endpoints crash (3). Settings PATCH 405 (5). Agent commands 405 (1). |
| **Database** | ✅ Dữ liệu thật có trong DB (vouchers, proposals, bank_tx, soft_checks, anomaly flags, QnA audits) |
| **LLM** | ✅ Hoạt động tốt — gọi thật, trả lời tiếng Việt chuyên nghiệp, không leak reasoning |
| **Agent/Worker** | ✅ Đã chạy tạo dữ liệu (50 runs, 33 vouchers, 30 proposals) |
| **UI → API connectivity** | ❌ **BLOCKED** — P0-1 (path routing) + P0-2 (auth) khiến UI không thể call bất kỳ API nào |
| **UI rendering** | ⚠️ HTML/CSS/JS render đúng structure nhưng không có data do API blocked |

### Ước lượng effort fix

| Priority | Items | Est. effort |
|----------|-------|-------------|
| P0 (6 bugs) | Routing, auth, schema mismatch, report 500, OCR upload | 2-3 ngày dev |
| P1 (5 bugs) | Forecast fabrication, reconcile server-side, settings PATCH, journal fallbacks, report file gen | 3-5 ngày dev |
| P2 (11 bugs) | UI polish, data quality, CORS, theme conflict | 2-3 ngày dev |

### Recommended immediate fixes (ưu tiên cao nhất):

1. **P0-1:** Sửa nginx.conf: `proxy_pass http://agent-service:8000;` (bỏ trailing `/`) hoặc `proxy_pass http://agent-service:8000/agent/;` 
2. **P0-2:** Thêm `proxy_set_header X-API-Key $AGENT_API_KEY;` vào nginx config (inject từ k8s secret qua env)  
   HOẶC set `AGENT_AUTH_MODE: "none"` cho intra-cluster (UI → agent-service đều trong cùng namespace)
3. **P0-3 + P0-4:** Sửa `journal.js` và `risk.js` để gửi đúng field names theo API schema
4. **P0-5:** Sửa `main.py` reports endpoints — thay `AcctVoucher.period` bằng extract period từ `AcctVoucher.date`
5. **P0-6:** Sửa `ocr.js` — gửi `FormData` thật với `multipart/form-data` thay vì JSON

---

*Report generated by QA automation — commit `6606337`, 2026-02-12T02:15Z*
