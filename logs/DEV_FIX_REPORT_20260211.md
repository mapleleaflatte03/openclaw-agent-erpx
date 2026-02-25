# BÁO CÁO SỬA LỖI QA — 2026-02-11

**Commit:** `5f10efd` (main)  
**Người thực hiện:** Dev chính Accounting Agent Layer ERP AI Kế toán  
**Cơ sở:** QA Report `logs/QA_REPORT_20260210.md`  
**Hình ảnh deploy:** `accounting-agent-layer/agent-service:5f10efd`, `accounting-agent-layer/ui:b9b135a`

---

## 1. TỔNG QUAN

| Issue | Nguồn QA | Mức ưu tiên | Trạng thái |
|-------|----------|-------------|------------|
| ISSUE 1 — VN Feeder không chạy end-to-end | BUG-FEEDER-01 | P2 | ✅ ĐÃ SỬA |
| ISSUE 2 — UI thiếu ô period cho voucher_ingest | QA Report | P2 | ✅ ĐÃ SỬA |
| ISSUE 3 — Q&A trả lời sai/trống + lộ reasoning_chain | BUG-QNA-01, BUG-QNA-02 | P1 | ✅ ĐÃ SỬA |

---

## 2. CHI TIẾT SỬA LỖI

### ISSUE 1 — VN Feeder Engine

**Nguyên nhân gốc:** Trước đây feeder là script ngoài (`vn_invoice_feeder.py`) không chạy trong container. Endpoint `/vn_feeder/control` chỉ ghi file control JSON mà không khởi tạo tiến trình nào.

**Giải pháp:**
- Tạo file mới `src/accounting_agent/agent_service/vn_feeder_engine.py` — background thread engine chạy trong process agent-service
- Tải catalog dữ liệu VN (3 nguồn Kaggle) hoặc sinh 500 bản ghi tổng hợp nếu không có dữ liệu trên đĩa
- Thread-safe: `start_feeder()`, `stop_feeder()`, `inject_now()`, `is_running()`
- Ghi `feeder_status.json` với thống kê: running, total_events_today, sources, pct_consumed
- Ngưỡng reset 90%: khi đã dùng 90% bản ghi → reset lại từ đầu
- Sửa endpoint `/vn_feeder/status` đọc trạng thái thực từ thread
- Sửa endpoint `/vn_feeder/control` gọi trực tiếp engine functions
- **Bug phụ phát hiện khi test:** Port mặc định sai (30080 NodePort → 8000 in-container) — đã sửa

**Files thay đổi:**
- `src/accounting_agent/agent_service/vn_feeder_engine.py` (MỚI, ~270 dòng)
- `src/accounting_agent/agent_service/main.py` (endpoint rewrite)

### ISSUE 2 — UI Period Input

**Nguyên nhân gốc:** Tab "Tạo tác vụ" trong Streamlit không có ô nhập `period` cho `voucher_ingest` — backend trả 422 khi gửi request thiếu period.

**Giải pháp:**
- Thêm `st.text_input("Kỳ kế toán (YYYY-MM) *")` cho 3 run types: `voucher_ingest`, `journal_suggestion`, `bank_reconcile`
- Cập nhật `_period_required` set khớp với backend `_PERIOD_REQUIRED_RUN_TYPES` (8 run types)
- Default: tháng hiện tại, đánh dấu `*` (bắt buộc)

**Files thay đổi:**
- `src/accounting_agent/ui/app.py`

### ISSUE 3 — Q&A Routing + Prompt + reasoning_chain

**Nguyên nhân gốc (3 vấn đề):**
1. `_answer_journal_explanation()` match từ khóa "tài khoản"/"hạch toán" trong câu hỏi tổng quát → trả "Vui lòng cung cấp số chứng từ" TRƯỚC khi LLM có cơ hội xử lý
2. LLM prompt quá ngắn (6 rules, max_tokens=512) → trả lời thiếu chi tiết, bị `_clean_llm_answer` lọc quá mạnh
3. API response chứa `reasoning_chain` trong `meta` → rủi ro lộ thông tin nội bộ

**Giải pháp:**
1. **Routing:** `_answer_journal_explanation()` chỉ kích hoạt khi `_extract_voucher_no(question)` trả về giá trị → câu hỏi tổng quát đi thẳng LLM
2. **LLM context:** Thêm TT133 context enrichment qua `get_regulation_context(question)` truyền vào `_try_llm_answer`
3. **System prompt:** Tăng từ 6→9 rules, yêu cầu cụ thể: số TK Nợ/Có, ≥1 ví dụ VND, tham chiếu TT200/TT133
4. **max_tokens:** 512→1024 (đủ cho câu trả lời chi tiết)
5. **Answer cleaning:** Ngưỡng tiếng Anh lỏng hơn (20%→40% global, 30%→50% per-line), thêm whitelist từ khóa kế toán
6. **Quality gate:** Nếu LLM trả trống/fallback nhưng có TT133 context → build câu trả lời hữu ích từ TT133
7. **reasoning_chain:** Xóa khỏi `meta` trong API response + xóa expander hiển thị trong UI Q&A tab

**Files thay đổi:**
- `src/accounting_agent/flows/qna_accounting.py`
- `src/accounting_agent/llm/client.py`
- `src/accounting_agent/agent_service/main.py`
- `src/accounting_agent/ui/app.py`

---

## 3. KIỂM TRA TỰ ĐỘNG

### Ruff Lint
```
All checks passed! ✅
```

### Pytest
```
107 passed, 5 skipped, 0 failures ✅
```

Tests cập nhật:
- `tests/integration/test_p3_llm_wiring.py::test_qna_llm_fallback_path` — assertion sửa cho fallback TT133
- `tests/integration/test_p3_llm_wiring.py::test_qna_llm_error_falls_through` — assertion sửa cho fallback TT133

---

## 4. ACCEPTANCE TEST TRÊN K3S

### Test A — Q&A (3 câu hỏi)

| # | Câu hỏi | llm_used | TK numbers | VND examples | TT ref | reasoning_chain | Kết quả |
|---|---------|----------|------------|--------------|--------|-----------------|---------|
| 1 | So sánh TK 131 vs 331 trong bán/mua chịu | ✅ true | 131, 331, 152, 112 | 200.000.000, 150.000.000 | TT133/2016 | ❌ không có | ✅ PASS |
| 2 | Khi nào dùng TK 642 thay vì 641 | ✅ true | 642, 641, 111 | 50.000.000, 30.000.000 | TT133/2016 | ❌ không có | ✅ PASS |
| 3 | Khấu hao TSCĐ 30 triệu/3 năm phương pháp đường thẳng | ✅ true | 214, 215 | 10.000.000/năm, 833.333/tháng | TT133/2016 | ❌ không có | ✅ PASS |

### Test B — VN Feeder

| Bước | Lệnh | Kết quả | Trạng thái |
|------|-------|---------|-----------|
| 1 | GET /vn_feeder/status | `running=false, total_events_today=0` | ✅ PASS |
| 2 | POST /vn_feeder/control `{"action":"start"}` | `{"status":"ok","action":"start"}` | ✅ PASS |
| 3 | GET /vn_feeder/status (sau 45s) | `running=true, total_events_today=3, sources=3` | ✅ PASS |
| 4 | GET /runs | Có voucher_ingest runs mới, trigger_type=event, invoice_data có VND | ✅ PASS |
| 5 | POST /vn_feeder/control `{"action":"stop"}` | `{"status":"ok","action":"stop"}` | ✅ PASS |
| 6 | GET /vn_feeder/status | `running=false, total_events_today=5` | ✅ PASS |

### Test C — Regression

| Kiểm tra | Kết quả | Trạng thái |
|----------|---------|-----------|
| `/diagnostics/llm` → `base_url_masked="configured"` | OK, không lộ URL | ✅ PASS |
| `/diagnostics/llm` → `health="ok"` | OK | ✅ PASS |
| Manual voucher_ingest run | `run_id` created, status=queued | ✅ PASS |
| ERP chain hoạt động | Runs execute → status=success | ✅ PASS |

### Test D — UI Period

| Kiểm tra | Trạng thái |
|----------|-----------|
| Tab Tạo tác vụ: voucher_ingest có ô "Kỳ kế toán (YYYY-MM) *" | ✅ Đã thêm |
| Tab Tạo tác vụ: journal_suggestion có ô period | ✅ Đã thêm |
| Tab Tạo tác vụ: bank_reconcile có ô period | ✅ Đã thêm |
| Tab Q&A: không hiển thị reasoning_chain | ✅ Đã xóa |

---

## 5. TÓM TẮT

- **3/3 issues đã sửa** trong phạm vi cho phép (không refactor lớn kiến trúc)
- **4/4 acceptance criteria PASS**
- **107 tests pass, 0 failures**
- **Ruff lint: sạch**
- **Deploy:** agent-service + UI rebuilt và rollout thành công trên k3s namespace `accounting-agent-staging`
- **Commits:** `b9b135a` (fix chính), `5f10efd` (fix port feeder)

---

*Báo cáo tạo tự động — 2026-02-11T02:35Z*
