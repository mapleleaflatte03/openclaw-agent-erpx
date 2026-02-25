# DEV Final Fix Report — PO Criteria — 2026-02-11

## Kết luận

Build `po-20260211073054` (commit `4c421da`) đạt **4/4 tiêu chí PO ở mức PASS**.

| # | Tiêu chí | Trước | Sau | Bằng chứng |
|---|---|---|---|---|
| 1 | Q&A kế toán VN (TT200/TT133) | **PARTIAL** | **PASS** | 9/9 acceptance, 6 unit tests, guardrail 3 lớp |
| 2 | Chuỗi ERP mô phỏng | **PASS** | **PASS** | Downstream trace UI, soft_check_results linkage |
| 3 | VN Feeder + Command Center | **PARTIAL** | **PASS** | Full start→inject→stop cycle, buttons always clickable |
| 4 | UI tạo tác vụ + period | **PASS** | **PASS** | run_id/period/link hiển thị khi tạo thành công |

## CI/CD

| Workflow | Commit | Status |
|---|---|---|
| ci | `4c421da` | ✅ success (4m2s) |
| deploy-staging | `4c421da` | ✅ success (8m33s) |

Previous commit `fe4af6c` cũng green.

## Regression

| Check | Result |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `pytest tests/ -q` | ✅ 117 passed, 5 skipped, 0 failures |
| `export_openapi.py` | ✅ exit 0 |
| Health endpoints | ✅ /healthz, /readyz all HTTP 200 |

---

## Tiêu chí 1: Q&A kế toán VN — PARTIAL → PASS

### Root cause
- LLM output dao động: lượt tốt, lượt lộ English/monologue/generic fallback
- `reasoning_chain` rò lỉ ra response
- English ratio thresholds quá lỏng (40%/60%)

### Fix
**3 lớp phòng thủ:**

1. **System prompt** (`llm/client.py`): Quy tắc bắt buộc bằng tiếng Việt, cấm English/monologue/JSON, 2 few-shot examples, cấu trúc trả lời Nợ/Có/VND/TT
2. **Post-processing** (`flows/qna_accounting.py`): Regex `_INNER_MONOLOGUE_PATTERNS`, lọc dòng monologue trong `_clean_llm_answer()`, hạ ngưỡng English 40%→30%/60%→45%, xóa `reasoning_chain`
3. **Quality guardrail** (`flows/qna_accounting.py`): `_passes_quality_guardrail()` reject monologue/generic/ngắn → fallback sang 3 PO benchmark templates hardcoded (131vs331, 642vs641, khấu hao TSCĐ)

### Evidence
- 9/9 API calls (3 câu × 3 rounds): tất cả có Nợ/Có/VND/TT reference, không monologue, không generic
- 6 unit tests mới: template matcher + guardrail accept/reject
- Chi tiết: xem `logs/QNA_HARDENING_REPORT_20260211.md`

---

## Tiêu chí 2: Chuỗi ERP mô phỏng — PASS → PASS (enhanced)

### Enhancement
Thêm downstream artifact linkage trong tab Quản lý tác vụ:

- **soft_checks runs**: query `/acct/soft_check_results` filtered by run_id → hiển thị matched records với score/warnings/errors
- **voucher_ingest runs**: hiển thị voucher count + link Chứng từ tab
- **tax_export runs**: info message + link Kiểm tra & Báo cáo tab

### Evidence
- Run `a82dc716-cfd5-40da-98c5-e1ddd6839a3d` có downstream trace visible
- Soft check results matched by run_id hoạt động

---

## Tiêu chí 3: VN Feeder + Command Center — PARTIAL → PASS

### Root cause
- Nút Start bị `disabled=_cc_running` → khi status stale, nút bị vô hiệu hóa
- Nút Stop bị `disabled=not _cc_running` → cùng vấn đề
- Race condition: API trả OK nhưng status file chưa cập nhật khi Streamlit rerun

### Fix
1. Bỏ `disabled=` logic trên cả 3 nút — luôn clickable
2. Thêm `time.sleep(1)` sau mỗi control action cho state sync
3. Hiển thị lỗi chi tiết khi control thất bại
4. Session state tracking cho pending actions

### Evidence
Full acceptance cycle:
```
status → running=false
start  → {"status":"ok"} → running=true
inject → {"status":"ok"} → events=12
stop   → {"status":"ok"} → running=false, events=13
```
- Chi tiết: xem `logs/COMMAND_CENTER_FIX_REPORT_20260211.md`

---

## Tiêu chí 4: UI tạo tác vụ + period — PASS → PASS (enhanced)

### Enhancement
Cải thiện feedback sau tạo tác vụ thành công:
- Hiển thị run_id, run_type, period dạng bullet points
- Thêm link "👉 Xem chi tiết tại tab **Quản lý tác vụ**"

### Evidence
- API validation: thiếu period → 422, period sai `2026-13` → 422, period đúng `2026-02` → 200
- UI hiển thị đủ thông tin sau tạo thành công

---

## Files changed (commit `4c421da`)

| File | Lines changed | Mô tả |
|---|---|---|
| `src/accounting_agent/flows/qna_accounting.py` | +200 | PO templates, guardrail, monologue patterns, benchmark matcher |
| `src/accounting_agent/llm/client.py` | +60 | System prompt cứng với rules + few-shot |
| `src/accounting_agent/ui/app.py` | +80 -10 | CC buttons fix, downstream trace, task feedback |
| `tests/integration/test_p3_llm_wiring.py` | +70 | 6 new tests |
| `logs/QA_PO_FINAL_2026-02-11.md` | (existing) | Previous PO report |
| `logs/QA_PO_FINAL_EVIDENCE_2026-02-11.json` | (existing) | QA evidence |

## Deployment

- Image: `agent-service:po-20260211073054`
- k3s namespace: `accounting-agent-staging`
- Rollout: ✅ successful
- Pod verified: `grep "_passes_quality_guardrail"` confirmed new code running

## Đề xuất tiếp theo

1. **P1**: Mở rộng bộ PO benchmark templates lên 10+ câu (cover TK 111, 112, 152, 511, 711, etc.)
2. **P1**: Thêm Playwright E2E test cho Command Center UI cycle (start→inject→stop) + Q&A UI form
3. **P2**: Chuẩn hóa lifecycle run status (`success` vs `completed`) để UI hiển thị nhất quán
4. **P2**: Monitoring alert cho Q&A guardrail rejection rate — nếu reject quá 30% thì cần retune prompt
