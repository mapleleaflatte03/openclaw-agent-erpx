# BÁO CÁO KIỂM THỬ THỦ CÔNG — ERP-X AI KẾ TOÁN (OpenClaw Agent ERPX)

**Ngày:** 2026-02-10  
**Tester:** QA (automated + cross-verified qua API + logs)  
**Môi trường:** Staging — k3s, `app.welliam.codes`  
**Backend version:** `028befdf3c7a66a312e79fef260def848c002b7b`  
**Smoke test:** 210/210 pass (100%), 0 key leaks  

---

## TỔNG KẾT

| Thống kê | Giá trị |
|----------|---------|
| Tổng số test case | 35 |
| ✅ OK | 23 |
| ❌ BUG | 5 |
| ⚠️ WARN | 7 |

### Phân loại BUG theo mức nghiêm trọng

| # | Bug | Mức độ | Nhóm |
|---|-----|--------|------|
| B1 | `USE_REAL_LLM` không được set trong k3s deployment → LLM toàn bộ chạy fallback rule-based | **P0 — CRITICAL** | LLM |
| B2 | Tạo run thiếu `period` → backend chấp nhận (HTTP 200) nhưng fail ngay → UI không chặn | P1 — High | Nghiệp vụ |
| B3 | Soft-check results thiếu trường `rule`/`check_type` — chỉ có aggregate stats | P2 — Medium | Nghiệp vụ |
| B4 | Q&A không gọi LLM thật cho câu hỏi nghiệp vụ (hệ quả của B1) | **P0 — CRITICAL** | LLM |
| B5 | `/diagnostics/llm` lộ full `base_url` DO Agent endpoint | P2 — Medium | Bảo mật |

---

## CHI TIẾT THEO TAB

### 0. ĐIỀU KIỆN TIÊN QUYẾT

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| Healthz | `GET /healthz` | `ok` | `{"status":"ok"}` | ✅ OK |
| Readyz | `GET /readyz` | `ready` | `{"status":"ready"}` — DB/Redis/S3 sẵn sàng | ✅ OK |
| LLM diagnostics | `GET /diagnostics/llm` | LLM phản hồi | `status=ok`, latency=1942ms, model=`openai-gpt-oss-120b` | ✅ OK |
| **USE_REAL_LLM** | Kiểm tra env k3s pod | `=true` trong pod | **KHÔNG CÓ** trong ConfigMap/Secret. `.env` có nhưng chỉ dùng cho docker-compose local. Default = `false`. | ❌ **BUG B1** |

**Chi tiết B1:** Secret `agent-llm` chứa `DO_AGENT_BASE_URL`, `DO_AGENT_API_KEY`, `DO_AGENT_MODEL` — nhưng thiếu `USE_REAL_LLM=true`. Code tại `llm/client.py:60` check `os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")` → evaluates to `False`.

**Fix đề xuất:**
```bash
kubectl patch configmap agent-config -n openclaw-agent-staging \
  --type merge -p '{"data":{"USE_REAL_LLM":"true"}}'
kubectl rollout restart deploy/agent-service deploy/agent-worker-standby -n openclaw-agent-staging
```

---

### 1. TAB TẠO TÁC VỤ

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 1.1 Tạo run đầy đủ | `POST /runs` type=soft_checks, period=2026-01 | Run tạo thành công, queued → success | `run_id=bae13213`, status → `success`, stats: `{exceptions: 5}` trong ≤25s | ✅ OK |
| 1.1b Transition | Poll run | queued → running → success | Final: `success` | ✅ OK |
| **1.2 Bỏ trống Kỳ** | `POST /runs` không có period | UI/API chặn, báo lỗi rõ | HTTP 200 trả run_id nhưng `status=failed`. Không có validation error. | ❌ **BUG B2** |
| 1.3 Tạo 2 run liên tiếp + refresh | Tạo bank_reconcile + voucher_classify, GET /runs | Cả 2 xuất hiện | Tìm thấy cả 2 run, tổng 20 items. Trạng thái đúng. | ✅ OK |

**Chi tiết B2:** Backend nhận request thiếu `period`, tạo run mới (HTTP 200), nhưng worker fail vì thiếu dữ liệu → `status=failed`. UI cần validate phía client trước khi gửi, hoặc API cần trả 422 Validation Error.

---

### 2. TAB CHỨNG TỪ

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 2.1 Danh sách chứng từ (sau ingest) | `GET /acct/vouchers` | Có danh sách OCR/chuẩn hoá | 3 vouchers: số HĐ (`0000123`, `PC0001`, `PT0001`), MST, tên NCC, ngày, loại chứng từ đầy đủ | ✅ OK |
| 2.2 Không lộ URI nội bộ | Kiểm tra JSON | Không chứa minio/s3/localhost | Không tìm thấy URI nội bộ nào | ✅ OK |
| 2.3 Thống kê phân loại | `GET /acct/voucher_classification_stats` | Có thống kê | HTTP 200, `{"stats":[]}` (ban đầu trống, cần chạy classify) | ⚠️ WARN |
| 2.4 Upload file không hỗ trợ | Cần test qua UI (Streamlit upload widget) | File lỗi báo riêng | Không thể test qua API — cần test UI trực tiếp | ⚠️ WARN (untested) |
| 2.5 Re-ingest cùng file | Chạy voucher_ingest lần 2 | Phát hiện duplicate | `stats:{count_new_vouchers:0, skipped_existing:3}` — đúng! | ✅ OK |

**Mẫu dữ liệu chứng từ:**
```json
{
  "voucher_no": "0000123",
  "voucher_type": "sell_invoice",
  "amount": 11000000.0,
  "partner_name": "CÔNG TY CP XYZ",
  "partner_tax_code": "0318765432",
  "description": "Bán hàng hóa theo hợp đồng 01/2025",
  "source": "mock_vn_fixture"
}
```

---

### 3. TAB BÚT TOÁN ĐỀ XUẤT

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 3.1 Danh sách bút toán | `GET /acct/journal_proposals` | Có Nợ/Có, giải thích TV | 5+ proposals, mỗi cái có 2 dòng Nợ/Có, `reasoning` tiếng Việt | ✅ OK |
| 3.2 Cân đối Nợ/Có | Kiểm tra sum(Nợ)=sum(Có) | Cân đối | ✅ Nợ 640,000 = Có 640,000 (ví dụ proposal 1) | ✅ OK |
| 3.3 TK khớp nghiệp vụ | So sánh logic | payment → 331/112; sell_invoice → 131/511; CPQLDN → 642/111 | Đúng logic kế toán VN cơ bản | ✅ OK |
| 3.4 Chấp nhận | `POST .../review status=approved` | Lưu ở Agent, không ghi ERP | `{"status":"approved"}` — chỉ cập nhật agent DB | ✅ OK |
| 3.5 Từ chối + lý do | `POST .../review status=rejected` | Lưu lý do, chứng từ còn | `{"status":"rejected"}`, comment lưu đầy đủ | ✅ OK |
| 3.6 Chặn re-review | Approve bút toán đã rejected | Báo lỗi | `"Bút toán đã được xử lý (trạng thái: rejected). Không thể thay đổi."` | ✅ OK |

**Mẫu bút toán đề xuất:**
```json
{
  "description": "Mua thiết bị máy tính",
  "confidence": 0.9,
  "reasoning": "Voucher type 'payment' → Nợ TK 331 (Phải trả người bán), Có TK 112 (Tiền gửi ngân hàng). Rule-based classification.",
  "lines": [
    {"account_code": "331", "account_name": "Phải trả người bán", "debit": 640000, "credit": 0},
    {"account_code": "112", "account_name": "Tiền gửi ngân hàng", "debit": 0, "credit": 640000}
  ]
}
```

---

### 4. TAB ĐỐI CHIẾU & GIAO DỊCH BẤT THƯỜNG

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 4.1 Danh sách anomaly | `GET /acct/anomaly_flags` | Có flags với lý do | 7 items, mỗi cái có `anomaly_type`, `severity`, `description` | ✅ OK |
| 4.2 Chi tiết có lý do | Kiểm tra trường | Có reason/description | Có các trường: `anomaly_type`, `severity`, `description`, `resolution` | ✅ OK |
| 4.3 Giao dịch ngân hàng | `GET /acct/bank_transactions` | Có danh sách | 10 items, đầy đủ thông tin | ✅ OK |
| 4.4 Đổi ngưỡng lệch | Cần test qua UI | Output thay đổi | Không test được qua API đơn thuần — cần UI | ⚠️ WARN (untested) |

---

### 5. TAB KIỂM TRA THIẾU / SAI CHỨNG TỪ (Soft Checks)

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 5.1 Danh sách soft-check | `GET /acct/soft_check_results` | Có lỗi hợp lý | 6 items | ✅ OK |
| **5.2 Chi tiết có rule** | Xem trường rule/check_type | Có rule cụ thể | **Thiếu trường `rule`/`check_type`**. Chỉ có aggregate: `total_checks`, `passed`, `warnings`, `errors`, `score` | ❌ **BUG B3** |
| 5.3 Validation issues | `GET /acct/validation_issues` | Có danh sách | 10 items | ✅ OK |
| 5.4 Không lộ nội bộ | Kiểm tra JSON | Không có URI nội bộ | Sạch — không lộ minio/postgres/localhost | ✅ OK |

**Chi tiết B3:** API `soft_check_results` trả aggregate stats (total/pass/warn/error/score) nhưng thiếu chi tiết từng rule vi phạm. Kế toán cần biết **cụ thể** chứng từ nào vi phạm rule nào (thiếu MST, thiếu số HĐ...). Thông tin chi tiết có thể nằm trong `validation_issues` (10 items).

---

### 6. TAB BÁO CÁO TÀI CHÍNH

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 6.1 Danh sách snapshot | `GET /acct/report_snapshots` | Có snapshot BCTC | 0 items — chưa có dữ liệu snapshot | ⚠️ WARN |

**Ghi chú:** Cần chạy workflow tạo report snapshot trước. Hiện tại chưa có dữ liệu báo cáo vì cần chạy `tax_export` hoặc tương đương.

---

### 7. TAB CHỈ SỐ & PHÂN TÍCH XU HƯỚNG

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 7.1 Biểu đồ xu hướng | Cần ≥3 kỳ snapshot | Có biểu đồ | 0 snapshots → không có dữ liệu cho biểu đồ | ⚠️ WARN |

---

### 8. TAB DỰ BÁO DÒNG TIỀN

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 8.1 Dữ liệu forecast | `GET /acct/cashflow_forecast` | Có tồn đầu/thu/chi/tồn cuối | 6 items. Có `forecast_date`, `direction`, `amount`, `currency`, `source_type`, `confidence` | ✅ OK |
| 8.2 Trường dòng tiền | Kiểm tra schema | Có inflow/outflow/opening/closing | Schema dùng `direction` (in/out) + `amount` thay vì traditional fields. Hợp lý với mô hình forecast. | ⚠️ WARN |

---

### 9. TAB HỎI ĐÁP & DIỄN GIẢI NGHIỆP VỤ

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 9.1 Rule-based | Hỏi "Kỳ 2026-01 có bao nhiêu chứng từ?" | Nhanh, đúng | `"Trong tháng 1/2026, hệ thống ghi nhận 0 chứng từ đã ingest."` — source=`acct_db` | ✅ OK |
| **9.2 LLM (TK 131 vs 331)** | Hỏi khác biệt TK | Gọi LLM thật, trả lời chuyên sâu | Trả lời generic: `"Để giải thích bút toán, vui lòng cung cấp số chứng từ cụ thể"` — **KHÔNG gọi LLM** (do B1) | ❌ **BUG B4** |
| 9.3 Câu nhạy cảm | Hỏi "làm sao lách thuế" | Từ chối/cảnh báo | `"Xin lỗi, tôi chưa hiểu câu hỏi này"` — rule-based fallback, an toàn nhưng không thực sự "từ chối" | ✅ OK (safe fallback) |
| 9.4 Giải thích bút toán | Hỏi "Vì sao Nợ 642/Có 331?" | Giải thích gắn context | `"Không tìm thấy chứng từ số chi"` — rule-based lookup, chưa giải thích logic | ⚠️ WARN |
| 9.5 Q&A audit log | `GET /acct/qna_audits` | Có lịch sử hỏi | Có log đầy đủ 4 câu hỏi vừa test | ✅ OK |

**Chi tiết B4:** Log backend cho thấy 4 lần `POST /acct/qna` nhưng KHÔNG có request nào đi tới `chat/completions`. Tất cả Q&A chạy rule-based. LLM chỉ được gọi khi chạy `/diagnostics/llm` (luôn bypass `USE_REAL_LLM` check). Hệ quả trực tiếp của B1.

---

### 10. TAB CẤU HÌNH / LABS

| Case | Bước | Kỳ vọng | Thực tế | Kết quả |
|------|------|---------|---------|---------|
| 10.1 Không lộ API key | `GET /diagnostics/llm` | Chỉ tên model | Key: ❌ an toàn. Nhưng `base_url` lộ full endpoint | ⚠️ **WARN (B5)** |
| 10.2 Metrics không lộ key | `GET /metrics` | Không key | Sạch — không có key trong metrics | ✅ OK |

**Chi tiết B5:** Response `/diagnostics/llm` chứa `"base_url": "https://brjbjkxv7hpmonuhwdk3zdus.agents.do-ai.run"`. Mặc dù không phải API key, full URL endpoint của DO Agent có thể bị lạm dụng nếu kẻ tấn công kết hợp với key bị lộ từ nơi khác. Nên thay bằng masked value.

---

### Cross-cutting: Nguyên tắc Chỉ Đọc ERP

| Case | Kết quả | Chi tiết |
|------|---------|---------|
| Agent không ghi ERP | ✅ OK | Kiến trúc xác nhận: Agent ghi vào `agent_*` tables. `erpx-mock-api` chỉ expose read endpoints (`GET /erp/v1/*`). Không có endpoint POST/PUT/DELETE nào đến ERP. |
| Maker-checker cho proposals | ✅ OK | Bút toán approved/rejected chỉ cập nhật `acct_journal_proposals` trong agent DB. |
| Idempotency guard | ✅ OK | Re-review bị chặn: `"Bút toán đã được xử lý. Không thể thay đổi."` |

---

## DANH SÁCH BUG ƯU TIÊN

### 🔴 P0 — CRITICAL (cần fix trước khi go-live)

1. **B1: `USE_REAL_LLM` thiếu trong k3s deployment**
   - **Ảnh hưởng:** Toàn bộ tính năng LLM vô hiệu hoá (Q&A, journal refinement, soft-check explanation)
   - **Nguyên nhân:** `.env` có `USE_REAL_LLM=true` nhưng k3s không inject biến này vào pods
   - **Fix:**  
     ```bash
     kubectl patch configmap agent-config -n openclaw-agent-staging \
       --type merge -p '{"data":{"USE_REAL_LLM":"true"}}'
     kubectl rollout restart deploy -n openclaw-agent-staging
     ```
   - **Verify:** Sau restart, gọi `/acct/qna` với câu nghiệp vụ → log phải thấy `POST .../chat/completions`

2. **B4: Q&A không gọi LLM thật** (hệ quả B1)
   - Tự fix khi B1 được fix
   - Cần re-test toàn bộ Tab 9 sau fix

### 🟡 P1 — HIGH

3. **B2: Tạo run thiếu `period` → backend chấp nhận rồi fail**
   - **Ảnh hưởng:** UX kém, user confused
   - **Fix:** Thêm validation trong `create_run()` endpoint: nếu `run_type` cần `period` → trả 422 khi thiếu

### 🟢 P2 — MEDIUM

4. **B3: Soft-check results thiếu chi tiết rule**
   - **Ảnh hưởng:** Kế toán không thấy cụ thể rule nào vi phạm
   - **Fix:** Hoặc link soft_check → validation_issues, hoặc inline chi tiết trong response

5. **B5: Lộ DO Agent base_url**
   - **Ảnh hưởng:** Thấp (không phải key) nhưng nên mask
   - **Fix:** Replace bằng `"***"` hoặc `"configured"`

---

## ITEMS CHƯA TEST (cần test UI trực tiếp)

| # | Case | Lý do |
|---|------|-------|
| 1 | Upload PDF hóa đơn VN qua UI | Cần Streamlit file upload widget |
| 2 | Upload 5 file + 1 file lỗi | Cần Streamlit UI |
| 3 | Preview chứng từ (ảnh/PDF) | Cần Streamlit UI |
| 4 | Đổi ngưỡng lệch đối chiếu | Cần UI config panel |
| 5 | Biểu đồ xu hướng + tooltip hover | Cần Streamlit chart |
| 6 | So sánh 2 kịch bản dòng tiền | Cần UI scenario builder |
| 7 | Tải PDF báo cáo | Cần UI download button |
| 8 | Export danh sách lỗi soft-check | Cần UI export |
| 9 | Bật/tắt LLM toggle trên UI | Cần UI settings page |
| 10 | Filter chi nhánh/đơn vị | Cần UI filter controls |

---

## KẾT LUẬN

Hệ thống OpenClaw Agent ERPX staging **hoạt động ổn định** về mặt hạ tầng (210/210 smoke, API responsive, k3s healthy). Nguyên tắc **đọc-ERP-chỉ-đề-xuất** được tuân thủ đúng.

**Tuy nhiên, có 1 lỗi P0 CRITICAL:** `USE_REAL_LLM` không được inject vào k3s deployment → toàn bộ tính năng AI/LLM chạy ở chế độ rule-based fallback. Cần patch configmap và restart pods trước khi coi hệ thống là "LLM thật đang chạy".

Sau khi fix B1, cần re-test:
- Tab 9 (Q&A) — xác nhận LLM thật trả lời câu hỏi nghiệp vụ
- Tab 3 (Journal suggestion) — xác nhận LLM refine bút toán rule-based
- Tab 5 (Soft checks) — xác nhận LLM giải thích issues bằng TV tự nhiên

**Smoke test 210 vòng 100% pass** xác nhận tính ổn định, nhưng smoke chạy ở cùng môi trường thiếu `USE_REAL_LLM` → kết quả smoke không chứng minh LLM thật hoạt động.
