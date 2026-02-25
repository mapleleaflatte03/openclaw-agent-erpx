# BÁO CÁO ACCEPTANCE (Blackbox) — Accounting Agent Layer ERP AI Kế toán

**Ngày kiểm thử:** 2026-02-10  
**Vai trò:** QA Tester / Acceptance Engineer (blackbox)  
**Phạm vi:** Kiểm thử hệ thống đang chạy trên `https://app.welliam.codes/` + đối chiếu qua API/Repo khi cần  
**Ràng buộc đã tuân thủ:** Không sửa code, không đổi config/prod infra, không in/log secret (API key/token) và không ghi rõ URL/hostname nội bộ trong báo cáo

## 1) Tổng quan kết quả (so với ý tưởng PO)

| Mục tiêu | Kết quả |
|---|---|
| Agent kế toán chạy full chain trên dữ liệu VN (synthetic + Kaggle + demo) | ⚠️ PARTIAL (API có các run_type/outputs, nhưng “data stream sống” không hoạt động) |
| VN Invoice Data Stream bắn event ngẫu nhiên 1–5/min | ❌ FAIL |
| Command Center quan sát/điều khiển được pipeline như “cockpit” | ❌ FAIL |
| Read-only nghiêm (human approve bắt buộc cho proposals) | ✅ PASS (đã thấy guard chống double-approve; UI có ghi rõ read-only) |
| LLM Q&A nghiệp vụ kế toán VN (tiếng Việt, đúng chuẩn) | ❌ FAIL (nhiều câu trả generic + có hallucination sai nghiệp vụ) |

## 2) Evidence (Screenshots)

Tất cả ảnh nằm tại `output/playwright/`:
- Multi viewport initial: `01-initial-ui-desktop-wide.png`, `02-initial-ui-desktop.png`, `03-initial-ui-tablet.png`, `04-initial-ui-mobile.png`
- Cross-browser @1440x900: `xbrowser-chromium-initial-1440x900-20260210.png`, `xbrowser-firefox-initial-1440x900-20260210.png`, `xbrowser-webkit-initial-1440x900-20260210.png`
- Tabs overview: `ui-00-sidebar-tabs.png`
- Runs/Journal/Anomaly/Cashflow/Vouchers/Q&A/Contracts/Command Center:
  `ui-13-tab-runs.png`, `ui-14-tab-journal-proposals.png`, `ui-15-tab-anomaly-flags.png`, `ui-17-tab-cashflow.png`, `ui-18-tab-vouchers.png`, `ui-19-tab-qna.png`, `ui-20-tab-contract-labs.png`, `ui-21-tab-command-center.png`
- Trigger form regression (thiếu input cần thiết): `acc-03-trigger-voucher-ingest-selected.png`, `acc-04-trigger-voucher-ingest-run-error.png`, `acc-07-trigger-tax-export-selected.png`
- Command Center controls nhưng không có hiệu ứng: `acc-11-tab-command-center.png`, `acc-12-command-center-after-inject-click.png`
- Q&A sai/không ổn định: `acc-06-qna-history-expanded.png`

## 3) Test 1 — Health + Regression nhanh

### 3.1 Kết quả endpoint health/readiness

- `GET /healthz` trên web host: **HTTP 200**, body = `"ok"` (content-type trả về `text/html`) ✅
- `GET /readyz` trên web host: **HTTP 200** nhưng trả về **HTML** (không phải JSON readiness) ⚠️
- `GET /agent/v1/healthz` trên web host: **HTTP 200** nhưng trả về **HTML** (không phải API JSON) ❌

Nhận xét: đường dẫn `/agent/v1/*` trên host public đang **không expose Agent API như mô tả môi trường** (bị “đè” bởi HTML/Streamlit). Điều này ảnh hưởng trực tiếp tới integration/automation qua domain public.

### 3.2 X-API-Key gate

- `GET /agent/v1/runs` (Agent API nội bộ, đã che endpoint): không có `X-API-Key` → **401** ✅; có `X-API-Key` → **200** ✅

### 3.3 UI regression nhanh theo tab

- Các tab UI render được (bằng Playwright), nhưng có lỗi nghiệp vụ ở phần Trigger/Q&A/Command Center (chi tiết ở các mục dưới).
- Table UX: có search/fullscreen (Streamlit dataframe), nhưng chưa thấy pagination server-side/filters rõ ràng theo kỳ.

**JSON mẫu (đã che):**
```json
{ "status": "ok" }
```

## 4) Test 2 — Q&A nghiệp vụ kế toán (LLM path)

### 4.1 Qua UI

- Q&A history có trường hợp trả lời **sai nghiệp vụ** (ví dụ mapping tài khoản sai) và có lúc trả lời generic “Xin lỗi…” (evidence: `acc-06-qna-history-expanded.png`).  

### 4.2 Qua API `/agent/v1/acct/qna`

Đã hỏi 3 câu nghiệp vụ (không rule-based đơn giản):
- Câu 1/2 trả về generic failure message mặc dù `meta.llm_used=true`.
- Câu 3 có nội dung trả lời nhưng **sai nghiệp vụ kế toán** (ví dụ: diễn giải TK 133/TK 242 không đúng chuẩn VN).

**Đánh giá theo tiêu chí yêu cầu:**
- `meta.llm_used = true`: ✅ (có dùng LLM)
- `meta.used_models` có model reasoning: ✅
- Ngôn ngữ tiếng Việt: ✅
- **Đúng chuẩn kế toán VN:** ❌ (hallucination/nhầm tài khoản là P0 vì gây quyết định sai)
- “Không lộ chain-of-thought”: ⚠️ RỦI RO — API trả `meta.reasoning_chain` và UI có phần “Chi tiết xử lý” hiển thị chuỗi này; quan sát cho thấy chuỗi này chủ yếu ASCII (khả năng tiếng Anh) → không phù hợp với yêu cầu “không lộ CoT”.

**JSON mẫu (đã che ID/chain):**
```json
{
  "answer": "Xin lỗi, hệ thống chưa thể đưa ra câu trả lời rõ ràng. Vui lòng thử lại hoặc diễn đạt câu hỏi theo cách khác.",
  "meta": {
    "llm_used": true,
    "used_models": ["OpenAI GPT-oss-120b"]
  }
}
```

## 5) Test 3 — Validation run + period check

### 5.1 Qua API `/agent/v1/runs`

- Thiếu `payload.period` cho `run_type` cần kỳ (ví dụ `tax_export`) → **HTTP 422**, thông báo tiếng Việt, nêu format `YYYY-MM` ✅
- `period` sai format (`2026-13`) → **HTTP 422** ✅
- `period` hợp lệ (`2026-01`) → **HTTP 200**, tạo run `status=queued` ✅

**JSON mẫu (422):**
```json
{ "detail": "period là bắt buộc cho run_type=tax_export, định dạng YYYY-MM (ví dụ 2026-01)." }
```

### 5.2 Qua UI “Tạo tác vụ”

- Regression UI: chọn `voucher_ingest` / `tax_export` nhưng **không render input kỳ (period)** → bấm chạy có thể lỗi (evidence: `acc-07-trigger-tax-export-selected.png`, `acc-04-trigger-voucher-ingest-run-error.png`). ❌

## 6) Test 4 — Validation issues drilldown

### 6.1 Qua API

- `GET /agent/v1/acct/soft_check_results?limit=1` lấy được 1 kỳ (ví dụ `2026-02`) ✅
- `GET /agent/v1/acct/validation_issues?check_result_id=<id>`:
  - số item trả về: 5
  - **100% items** có cùng `check_result_id` (distinct = 1) ✅

### 6.2 Qua UI

- Chưa thấy drilldown UI theo `check_result_id` (từ summary sang list lỗi chi tiết tương ứng). ⚠️

## 7) Test 5 — Diagnostics LLM (không lộ BASE_URL)

- `GET /diagnostics/llm` (Agent API nội bộ) trả **200** ✅
- Không còn field `base_url` thô ✅
- Có `base_url_masked="configured"`, có `model_name`, `health`, `latency_ms` ✅

**JSON mẫu (đã rút gọn):**
```json
{
  "status": "ok",
  "do_agent": {
    "base_url_masked": "configured",
    "model_name": "OpenAI GPT-oss-120b",
    "health": "ok"
  }
}
```

## 8) Test 6 — VN Invoice Data Stream (Feeder) + Command Center

### 8.1 Đối chiếu spec (đọc repo)

- `scripts/vn_data_catalog.py`: có 3 nguồn Kaggle (MC-OCR 2021, Receipt OCR, Appen VN OCR) + synthetic, thống kê tổng nguồn. ✅
- `scripts/vn_invoice_feeder.py`: mô tả loop random 1–5 event/phút, state SQLite, reset ~90%, ghi `feeder_status.json`. ✅

### 8.2 Hành vi thực tế trên hệ thống đang chạy

- UI tab “Command Center — VN Invoice Data Stream” hiển thị nhưng:
  - trạng thái “Đã dừng”
  - tổng sự kiện hôm nay = 0, trung bình/phút = 0
  - bảng nguồn dữ liệu trống
  - bấm “Khởi động/Inject ngay” **không làm thay đổi metrics** (evidence: `ui-21-tab-command-center.png`, `acc-12-command-center-after-inject-click.png`) ❌
- API điều khiển feeder:
  - `POST /agent/v1/vn_feeder/control` trả `{"status":"ok"}` ✅
  - nhưng `GET /agent/v1/vn_feeder/status` **không đổi** (luôn `running=false`, totals=0, sources=[]) ❌

**Kết luận:** Feeder/Command Center hiện tại **không tạo được cảm giác “hệ thống sống”** (không có sự kiện tăng theo phút, không có source consumption), không đạt mục tiêu PO.

## 9) Test 7 — End-to-end ERP chain (SIM)

Kỳ vọng PO: VN events → ingest → vouchers → checks → journal proposals → anomaly flags → cashflow/report cập nhật.

Thực tế:
- Do Feeder không chạy/Inject không có hiệu ứng, không thể xác nhận “dòng dữ liệu sống 1–5/min” kéo theo pipeline tự động. ❌
- Các khối dữ liệu/flows có tồn tại ở backend (runs/run_types), nhưng “chain tự chạy theo stream” không quan sát được qua Command Center/UI như yêu cầu cockpit. ⚠️

## 10) Sai lệch so với ý tưởng PO

- `/agent/v1/*` chưa được expose đúng trên domain public (đang trả về HTML cho một số path).  
- Command Center không phản ánh trạng thái thực (status không đổi dù control trả ok), không có metrics/sources consumption.  
- Q&A nghiệp vụ kế toán không đạt (generic failure + hallucination sai tài khoản).  
- UI Trigger không render input bắt buộc cho run_type cần `period`, gây lỗi khi vận hành bằng UI.

## 11) Danh sách BUG / Issues (P0/P1/P2)

### P0 — Critical

1) **Command Center/Feeder không hoạt động**
- Triệu chứng: totals=0, sources trống; Start/Inject không đổi status/metrics.
- Ảnh: `ui-21-tab-command-center.png`, `acc-12-command-center-after-inject-click.png`

2) **Public host không expose đúng `/agent/v1/*`**
- Triệu chứng: `GET https://app.welliam.codes/agent/v1/healthz` trả HTML thay vì JSON.
- Ảnh: `01-initial-ui-desktop-wide.png` (evidence context), và kiểm thử HTTP (không kèm URL nội bộ).

3) **Q&A nghiệp vụ có hallucination/sai tài khoản**
- Triệu chứng: trả lời nhầm bản chất TK (ví dụ TK 133/TK 242; trước đó thấy nhầm TK 131/TK 331 trong history).
- Ảnh: `acc-06-qna-history-expanded.png`

### P1 — High

4) **UI Trigger thiếu input bắt buộc (`period`/params) cho một số run_type**
- Triệu chứng: chọn `tax_export`/`voucher_ingest` nhưng không có field kỳ; bấm chạy gây lỗi/không tạo run đúng.
- Ảnh: `acc-07-trigger-tax-export-selected.png`, `acc-04-trigger-voucher-ingest-run-error.png`

5) **Rủi ro lộ chain-of-thought qua `reasoning_chain`**
- Triệu chứng: API trả `meta.reasoning_chain`; UI “Chi tiết xử lý” hiển thị chuỗi này (có vẻ tiếng Anh/ASCII).
- Tác động: không phù hợp yêu cầu “không lộ CoT”, đồng thời dễ gây nhiễu người dùng cuối.

6) **Rủi ro lộ thông tin nội bộ qua Dev/Debug/traceback**
- Triệu chứng: có màn hình/expander Dev/Debug và một số lỗi hiển thị traceback/error box.
- Tác động: lộ hostname/path nội bộ (không ghi rõ trong báo cáo này theo ràng buộc).

### P2 — Medium

7) **Thiếu drilldown UI theo `check_result_id`**
- API drilldown đúng, nhưng UI chưa có luồng click từ summary → danh sách lỗi chi tiết theo đúng ID.

8) **Table UX thiếu pagination/filter theo kỳ**
- Hiện chủ yếu dựa vào limit cố định; khó dùng khi dữ liệu tăng do stream.

## 12) Đề xuất test bổ sung (không sửa hệ thống)

1) Thêm 1 kịch bản acceptance “10 phút quan sát”: Command Center tăng đều 1–5 event/phút, sources luân phiên, vouchers/proposals/anomalies tăng tương ứng.  
2) Q&A: tạo bộ câu hỏi chuẩn TT133/TT200 (20–30 câu) + chấm đúng/sai; chặn hallucination bằng rule-check (account code dictionary).  
3) UI: test theo “task-first”: người dùng chỉ cần UI để chạy `voucher_ingest`/`soft_checks`/`tax_export` (không cần API), đảm bảo đầy đủ field bắt buộc + validation tiếng Việt.

