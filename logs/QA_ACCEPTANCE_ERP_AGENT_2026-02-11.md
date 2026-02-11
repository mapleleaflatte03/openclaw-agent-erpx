# QA Acceptance Report — ERP-X AI Kế toán (Staging)

**Ngày test:** 2026-02-11  
**Vai trò:** QA Tester (blackbox)  
**Web:** `https://app.welliam.codes/`  

## Môi trường test

- **Mode A (UI):** test trực tiếp trên web public.
- **Mode B (API nội bộ):** test qua `BASE=http://127.0.0.1:30080` với header `X-API-Key` (không ghi key).

---

## 1) Q&A kế toán VN (PO target #1) — **PARTIAL**

### Kết luận
- LLM wiring đã chạy đúng về mặt kỹ thuật (`llm_used=true`, không còn field `reasoning_chain` trong JSON API).
- Tuy nhiên chất lượng nghiệp vụ chưa ổn định: 1 câu trả tốt, 2 câu còn “tra cứu khung TT133/TT200” và không đi vào bút toán cụ thể như kỳ vọng PO.

### Evidence
- Mode A ảnh:
  - `output/playwright/qa-20260211-02-qna-answer-131-331.png`
  - `output/playwright/qa-20260211-03-qna-answer-642-641.png`
  - `output/playwright/qa-20260211-04-qna-answer-khauhao.png`
- Mode B API (rút gọn):
  - 3/3 request `POST /agent/v1/acct/qna` trả `200`.
  - `meta.llm_used = true` cho cả 3 câu.
  - `reasoning_chain` **không còn xuất hiện** trong `meta`.
  - `used_models` có model reasoning.
- Nội dung trả lời:
  - Câu 131 vs 331: có giải thích đúng hướng và nhắc TT200/TT133.
  - Câu 642 vs 641 và khấu hao 30 triệu/3 năm: trả lời còn chung chung, thiếu ví dụ bút toán Nợ/Có chi tiết theo yêu cầu.

---

## 2) UI Tạo tác vụ + period (PO target #4) — **PASS**

### Kết luận
- Form đã hiển thị period `YYYY-MM` cho các run_type test (`voucher_ingest`, `soft_checks`, `tax_export`).
- Chạy với period `2026-02` tạo tác vụ thành công; run xuất hiện và chuyển trạng thái tốt.

### Evidence
- Mode A ảnh:
  - `output/playwright/qa-20260211-05-trigger-voucher-period.png`
  - `output/playwright/qa-20260211-06-trigger-softchecks-period.png`
  - `output/playwright/qa-20260211-07-trigger-taxexport-period.png`
  - `output/playwright/qa-20260211-08-runs-after-create.png`
- Mode B API (rút gọn):
  - `voucher_ingest(period=2026-02)` -> `200`, final `success`.
  - `soft_checks(period=2026-02)` -> `200`, final `success`.
  - `tax_export(period=2026-02)` -> `200`, final `success`.

---

## 3) VN Feeder + Command Center (PO target #3) — **PASS**

### Kết luận
- Command Center hiển thị đúng trạng thái/metrics/sources.
- Start + Inject hoạt động: event tăng theo thời gian, có dữ liệu từ 3 nguồn.
- Stop hoạt động: `running=false`.

### Evidence
- Mode A ảnh:
  - Trước start: `output/playwright/qa-20260211-09-command-center-before-start.png`
  - Sau ~70s: `output/playwright/qa-20260211-10-command-center-after-70s.png`
- Snapshot UI sau chạy (~70s):
  - `running: true`
  - `total_events_today: 4`
  - `avg_events_per_min: 2.66`
  - có bảng “Nguồn dữ liệu”
- Mode B API (rút gọn):
  - Poll status: `(30s, running=true, total=2, sources=3)`, `(60s, total=2, sources=3)`, `(90s, total=4, sources=3)`
  - Status hiện tại khi kiểm tra: `total_events_today=7`, `avg_events_per_min=3.9`, `sources_sent_count=[2,4,1]`
  - `POST stop` thành công, `running=false`.

---

## 4) Regression nhanh (PO target #2 + an toàn) — **PASS**

### Kết luận
- Các tab chính load bình thường, không thấy traceback/500 trên UI test session.
- Chuỗi mô phỏng vẫn có dữ liệu trên các tab nghiệp vụ.
- Diagnostics LLM an toàn: còn mask, không lộ raw base URL/API key.

### Evidence
- Mode A ảnh regression:
  - `output/playwright/qa-20260211-12-tab-journal.png`
  - `output/playwright/qa-20260211-13-tab-anomaly.png`
  - `output/playwright/qa-20260211-14-tab-check-report.png`
  - `output/playwright/qa-20260211-15-tab-cashflow.png`
  - `output/playwright/qa-20260211-16-tab-runs-regression.png`
  - `output/playwright/qa-20260211-11-vouchers-after-feeder.png`
- Mode B API:
  - `GET /diagnostics/llm` -> `status: ok`
  - `do_agent.base_url_masked = configured`
  - không có field raw `base_url`.

---

## Tổng hợp trạng thái 4 tiêu chí PO

1. Q&A kế toán VN: **PARTIAL** (wiring tốt, chất lượng nội dung chưa ổn định).  
2. Chuỗi ERP mô phỏng: **PASS** (tab nghiệp vụ có dữ liệu và không vỡ regression trong phiên test).  
3. VN Feeder + Command Center: **PASS** (start/inject/metrics/sources hoạt động).  
4. UI Tạo tác vụ + period: **PASS** (đã có period và chạy được các run_type test).  

## Nếu cần FAIL/PARTIAL cần theo dõi tiếp

- Q&A chất lượng nội dung cho câu hỏi nghiệp vụ sâu (đặc biệt 642/641 và khấu hao): cần tăng tỷ lệ trả lời đi thẳng vào bút toán cụ thể, có ví dụ VND đầy đủ.
