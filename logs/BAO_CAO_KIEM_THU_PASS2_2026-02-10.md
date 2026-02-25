# BÁO CÁO KIỂM THỬ TAY — PASS 2 (Re-test sau fix)
**Ngày:** 2026-02-10 10:30 UTC  
**Tester:** QA-Agent (automated)  
**Phiên bản:** `f5a378301896b65179b490f8890c5e9be3e69267`  
**Môi trường:** k3s staging — namespace `accounting-agent-staging`  
**Đối tượng:** Sửa lỗi B1 (USE_REAL_LLM) + B6 (reasoning_content) và kiểm tra lại các bug còn mở

---

## 1. TÓM TẮT THAY ĐỔI

| Hành động | Chi tiết |
|-----------|---------|
| **Patch ConfigMap** | `kubectl patch configmap agent-config` — thêm `USE_REAL_LLM=true` |
| **Fix code B6** | `client.py` — fallback `reasoning_content` khi `content` rỗng (reasoning models) |
| **Fix code B6b** | `client.py` — thêm `used_models` list vào `generate_qna_answer()` |
| **Build & Deploy** | Image `f5a3783…` → `docker build` → `k3s ctr images import` → `kubectl set image` |
| **Rollout** | agent-service + agent-worker-standby — cả 2 rolled out successfully |

---

## 2. KẾT QUẢ KIỂM TRA

### 2.1 B1 — USE_REAL_LLM không được inject (P0 CRITICAL)

| Kiểm tra | Kết quả |
|----------|---------|
| `kubectl exec ... printenv USE_REAL_LLM` | `true` ✅ |
| Healthz sau restart | 200 OK ✅ |
| Readyz sau restart | 200 OK ✅ |
| Backend logs: `POST .../chat/completions "HTTP/1.1 200 OK"` | Có ✅ |

**Kết luận: B1 → FIXED ✅**

---

### 2.2 B4 + B6 — Q&A không gọi LLM / Trả lời rỗng (P0 CRITICAL)

#### Trước fix
- `answer: ""` — LLM được gọi (200 OK) nhưng response rỗng
- Root cause: Model GPT-oss-120b (reasoning model) trả output trong `reasoning_content`, không phải `content`

#### Sau fix (B6 — `reasoning_content` fallback)

| Câu hỏi | `llm_used` | `used_models` | `answer` length | Kết quả |
|----------|------------|---------------|-----------------|---------|
| "TK 131 là gì?" | `true` | `["OpenAI GPT-oss-120b"]` | ~800 chars | Có nội dung kế toán ✅ |
| "Khi nào dùng TK 642?" | `true` | `["OpenAI GPT-oss-120b"]` | 404 chars | JSON contract format ⚠️ |
| "So sánh TK 131 và 331" (trước fix) | `true` | `[]` | 0 chars | Rỗng ❌ |

**Kết luận:**
- **B4 → FIXED ✅** — LLM path hoạt động, `llm_used=true`, `used_models` populated
- **B6 → FIXED ✅** — `reasoning_content` fallback hoạt động
- **MỚI — B7 (P1 WARN)**: Chất lượng câu trả lời không ổn định:
  - Một số câu trả lời chứa raw CoT (chain-of-thought) thay vì clean text
  - Một số câu trả lại JSON format `{"tier":3, "decision":"missing_data"}` — do DO Agent model bị lẫn vai trò contract analysis
  - **Đề xuất**: Tăng cường system prompt hoặc post-process output để lọc CoT / JSON rác

---

### 2.3 B2 — Tạo run với period rỗng (P1)

```
POST /agent/v1/runs
Body: {"run_type":"soft_checks","trigger_type":"manual","payload":{}}
Response: 200 — run_id created (queued)
```

**B2 STILL OPEN ❌** — Endpoint chấp nhận payload rỗng (không có `period`).  
Cần validation: `if run_type in need_period_types and 'period' not in payload: raise 422`

---

### 2.4 B3 — Soft-check thiếu drill-down (P2)

| Endpoint | Filter support |
|----------|---------------|
| `GET /acct/soft_check_results` | ✅ Có `id`, `run_id` |
| `GET /acct/validation_issues` | Chỉ `resolution`, `severity` — **KHÔNG CÓ `check_result_id`** |

Data linkage tồn tại (`validation_issues.check_result_id` → `soft_check_results.id`) nhưng API không expose filter.

**B3 STILL OPEN ❌** — Cần thêm query param `check_result_id` vào endpoint `validation_issues`.

---

### 2.5 B5 — Diagnostics leak base_url (P2)

```json
GET /diagnostics/llm
→ "base_url":"https://brjbjkxv7hpmonuhwdk3zdus.agents.do-ai.run"
```

Full DO Agent endpoint ID exposed.

**B5 STILL OPEN ❌** — Cần mask: `https://brj***zdus.agents.do-ai.run`

---

## 3. REGRESSION SWEEP

| Tab/Feature | Kết quả | Chi tiết |
|-------------|---------|----------|
| Healthz / Readyz | ✅ OK | 200 cả hai |
| Vouchers | ✅ OK | 2+ items, có description, không leak URI |
| Journal Proposals | ✅ OK | Nợ=Có balanced, status correct |
| Anomaly Flags | ✅ OK | 3 items, type+severity+description đầy đủ |
| Cashflow | ⚠️ WARN | 0 items — chưa chạy `cashflow_forecast` run |
| Maker-Checker | ✅ OK | "Bút toán đã được xử lý... Không thể thay đổi" |
| Runs | ✅ OK | Statuses: success |
| Auth Guard | ✅ OK | "Không có quyền truy cập" khi thiếu API key |

**Kết luận**: Không có regression sau khi deploy bản fix.

---

## 4. BẢNG TỔNG HỢP BUG

| Bug ID | Mô tả | Severity | Trạng thái Pass 1 | Trạng thái Pass 2 |
|--------|--------|----------|-------------------|-------------------|
| **B1** | USE_REAL_LLM không inject vào k3s configmap | P0 CRITICAL | OPEN | **FIXED ✅** |
| **B2** | Create run chấp nhận payload rỗng (thiếu period) | P1 | OPEN | **STILL OPEN ❌** |
| **B3** | validation_issues không filter được theo check_result_id | P2 | OPEN | **STILL OPEN ❌** |
| **B4** | Q&A không gọi LLM (do B1) | P0 CRITICAL | OPEN | **FIXED ✅** (via B1 fix) |
| **B5** | Diagnostics leak full base_url | P2 | OPEN | **STILL OPEN ❌** |
| **B6** | LLM trả answer rỗng (reasoning_content) | P0 CRITICAL | — (mới phát hiện) | **FIXED ✅** |
| **B7** | LLM answer chứa raw CoT/JSON contract format | P1 WARN | — (mới phát hiện) | **NEW ⚠️** |

---

## 5. COMMIT & IMAGE

```
Commit: f5a378301896b65179b490f8890c5e9be3e69267
Message: fix(llm): handle reasoning_content from reasoning models (GPT-oss-120b)

Files changed:
  src/accounting_agent/llm/client.py
    - _chat(): fallback reasoning_content khi content rỗng
    - generate_qna_answer(): thêm used_models list

Image: ghcr.io/mapleleaflatte03/accounting-agent-layer/agent-service:f5a3783...
Deployed: agent-service + agent-worker-standby
```

---

## 6. ĐỀ XUẤT HÀNH ĐỘNG TIẾP THEO

### Cần fix ngay (Sprint hiện tại)
1. **B2**: Thêm validation `period` cho `soft_checks`, `cashflow_forecast`, `journal_suggestion` run types
2. **B7**: Post-process LLM output — strip CoT và normalize JSON contract responses thành plain text
3. **B3**: Thêm `check_result_id` filter vào `/acct/validation_issues` endpoint

### Nên fix (Sprint tiếp)
4. **B5**: Mask `base_url` trong `/diagnostics/llm` — chỉ hiện domain, không hiện agent ID

### Theo dõi
5. Chạy `cashflow_forecast` run để verify tab Cashflow có data
6. Monitor LLM latency — hiện tại ~1.4s per call (chấp nhận được)
7. Cân nhắc thay model hoặc fine-tune system prompt nếu B7 ảnh hưởng UX

---

*Report generated by QA-Agent — Pass 2: post-fix verification*
