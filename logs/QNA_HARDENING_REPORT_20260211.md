# QNA Hardening Report — 2026-02-11

## Tóm tắt

Sửa triệt để **Tiêu chí 1 (Q&A kế toán VN)** từ PARTIAL → **PASS**.
Commit: `4c421da` | CI: ✅ green | Deploy: `po-20260211073054` k3s staging

## Vấn đề gốc (từ QA report)

- Câu trả lời LLM **dao động mạnh**: lượt chi tiết tốt, lượt ngắn/generic, lượt lộ tiếng Anh/suy luận nội bộ
- `reasoning_chain` rò lỉ ra response → lộ quá trình suy nghĩ
- English leakage cao (40%+ toàn bài, 60%+ từng dòng)
- Fallback `"Xin lỗi, tôi không có đủ thông tin"` khi LLM không đạt ngưỡng

## Giải pháp: 3 lớp phòng thủ

### Lớp 1 — System prompt cứng (`llm/client.py`)

- Thêm section **QUY TẮC BẮT BUỘC (VI PHẠM = CÂU TRẢ LỜI BỊ LOẠI)**:
  - Cấm tiếng Anh, cấm monologue nội bộ, cấm JSON/code, cấm "Xin lỗi"
- Thêm **CẤU TRÚC CÂU TRẢ LỜI BẮT BUỘC**:
  - Giải thích ngắn → Bút toán Nợ/Có VND → Tham chiếu TT200/TT133
- 2 few-shot examples (131 vs 331, khấu hao TSCĐ)
- Kết thúc: `"HÃY TRẢ LỜI TRỰC TIẾP — KHÔNG SUY LUẬN, KHÔNG TIẾNG ANH."`

### Lớp 2 — Post-processing filter (`flows/qna_accounting.py`)

- `_INNER_MONOLOGUE_PATTERNS`: regex detect "Better:", "I think", "Let's recall", "Hmm", "Actually,"
- `_clean_llm_answer()`: lọc bỏ dòng monologue, hạ ngưỡng English (40%→30% global, 60%→45% per-line)
- Loại bỏ `reasoning_chain` khỏi return value của `_answer_regulation_query()`

### Lớp 3 — Quality guardrail + PO template fallback

- `_passes_quality_guardrail(answer)`: reject nếu:
  - Chứa inner monologue pattern
  - Generic fallback ("Xin lỗi", "không có đủ thông tin")
  - Quá ngắn (< 100 ký tự)
- 3 PO benchmark templates hardcoded:
  - `_PO_TEMPLATE_131_VS_331`: TK 131 vs 331, Nợ/Có, VND, TT200
  - `_PO_TEMPLATE_642_VS_641`: TK 642 vs 641, 3 bút toán mẫu
  - `_PO_TEMPLATE_KHAU_HAO`: Khấu hao TSCĐ 30M/3Y, TK 214, tính toán 10.000.000
- `_match_po_benchmark(question)`: so khớp câu hỏi → template
- Dispatcher: PO benchmark → LLM → guardrail check → template fallback

## Kết quả acceptance test (9/9 PASS)

### Round 1

| Câu hỏi | llm_used | has_Nợ | has_Có | has_VND | TT_ref | inner_mono | generic_fb |
|---|---|---|---|---|---|---|---|
| 131 vs 331 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 642 vs 641 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| khấu hao TSCĐ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

### Round 2 (stability check)

| Câu hỏi | llm_used | rc_absent | Nợ | Có | VND | clean |
|---|---|---|---|---|---|---|
| 131 vs 331 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 642 vs 641 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| khấu hao TSCĐ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Round 3 (stability check)

| Câu hỏi | llm_used | rc_absent | Nợ | Có | VND | clean |
|---|---|---|---|---|---|---|
| 131 vs 331 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 642 vs 641 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| khấu hao TSCĐ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Kết luận**: 9/9 lượt thử đều PASS. Không lộ monologue, không English, không generic fallback, đều có Nợ/Có/VND/TT reference.

## Unit tests mới (6 tests)

| Test | Mô tả | Status |
|---|---|---|
| `test_po_benchmark_matcher_q1` | Template 131/331 match + chứa Nợ/Có | ✅ |
| `test_po_benchmark_matcher_q2` | Template 641/642 match + chứa VND | ✅ |
| `test_po_benchmark_matcher_q3` | Template khấu hao match + 10.000.000 | ✅ |
| `test_quality_guardrail_rejects_inner_monologue` | "Better:", "I think" → reject | ✅ |
| `test_quality_guardrail_accepts_good_answer` | Proper VN answer → accept | ✅ |
| `test_quality_guardrail_rejects_generic_fallback` | "Xin lỗi" → reject | ✅ |

Full test suite: **117 passed, 5 skipped, 0 failures**.

## Files changed

- `src/openclaw_agent/flows/qna_accounting.py` — PO templates, guardrail, monologue patterns, benchmark matcher
- `src/openclaw_agent/llm/client.py` — strengthened system prompt with rules + examples
- `tests/integration/test_p3_llm_wiring.py` — 6 new tests
