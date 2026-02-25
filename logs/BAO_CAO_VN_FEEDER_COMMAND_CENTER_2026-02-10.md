# BÁO CÁO: VN INVOICE DATA STREAM + COMMAND CENTER

**Ngày:** 2026-02-10  
**Commit:** `cadeb17` (main)  
**CI:** ✅ GREEN  |  **Deploy-staging:** ✅ GREEN

---

## 1. Tổng quan

Triển khai hệ thống **VN Invoice Data Stream** sử dụng dữ liệu thực từ
3 bộ Kaggle datasets + synthetic data, kết hợp **Command Center** trên
giao diện Streamlit để quản lý luồng dữ liệu hoá đơn VN.

## 2. Các thành phần đã triển khai

### 2.1 VN Data Catalog (`scripts/vn_data_catalog.py`)
- **Schema thống nhất:** `VnInvoiceRecord` dataclass — source_name,
  external_id, issue_date, seller_name, seller_tax_code, buyer_name,
  buyer_tax_code, total_amount, vat_amount, currency, line_items, 
  file_paths, regulation_hint, raw_texts
- **Nguồn dữ liệu Kaggle:**
  - `MC-OCR 2021` — 1.151 bản ghi (KIE TSV + hình ảnh)
  - `Receipt OCR` — 1.114 bản ghi (line_annotation.txt)
  - `Appen VN OCR` — 15 bản ghi (labelme JSON — BILLS / TRADE DOCS / FORMS)
- **Tổng:** 2.280 bản ghi
- **Enrichment:** Tự động bổ sung seller/buyer/amount ngẫu nhiên cho
  các bản ghi thiếu thông tin

### 2.2 VN Invoice Feeder (`scripts/vn_invoice_feeder.py`)
- **Loop vô hạn:** 1–5 sự kiện/phút (cấu hình qua ENV)
- **State tracking:** SQLite DB tại `/data/vn_feeder_cache/feeder_state.db`
- **Auto-reset:** Khi ≥90% bản ghi đã gửi → reset toàn bộ state
- **Backoff:** Sau 10 lỗi liên tiếp → đợi 30s
- **CLI:**
  - `--max-events N` — dừng sau N sự kiện (dùng cho CI)
  - `--inject-once` — inject 1 batch rồi thoát
- **Control file:** Đọc từ `feeder_control.json` (start/stop/speed)
- **Status file:** Ghi ra `feeder_status.json` (running, total, avg_epm, sources)

### 2.3 TT133/2016/TT-BTC Regulation Index
- **Module:** `src/accounting_agent/regulations/tt133_index.py`
- **55 tài khoản** (Loại 1–9) theo Thông tư 133
- **13 bút toán mẫu** (mua hàng, bán hàng, lương, khấu hao, thuế...)
- **Tra cứu:** `lookup_account()`, `search_accounts()`, `suggest_journal_entry()`
- **LLM context:** `get_regulation_context()` — trả về chuỗi context cho prompt

### 2.4 Backend Endpoints
- `GET /agent/v1/vn_feeder/status` — trạng thái feeder (running, total_today, sources...)
- `POST /agent/v1/vn_feeder/control` — điều khiển (start/stop/inject_now, target_epm)
- OpenAPI spec đã cập nhật

### 2.5 Command Center UI Tab
- **Tab thứ 11:** "🎛️ Command Center (VN Agent)"
- **Sections:**
  - Badge trạng thái (Đang chạy / Đã dừng)
  - 4 metric cards (tổng sự kiện, trung bình/phút, sự kiện gần nhất)
  - Bảng nguồn dữ liệu (source, total, sent, % consumed)
  - Nút điều khiển: Khởi động / Dừng / Inject ngay
  - Slider tốc độ (1–10 sự kiện/phút)
  - Tra cứu TT133 nhanh (text input → kết quả inline)

### 2.6 Smoke Test (`scripts/smoke_vn_feeder.py`)
- Kiểm tra healthz, vn_feeder/status, vn_feeder/control
- Chạy feeder với `--max-events=5`
- Xác nhận runs được tạo, multiple sources used
- Kiểm tra TT133 module import + lookup

## 3. Files Changed

| File | Action | Lines |
|------|--------|-------|
| `scripts/vn_data_catalog.py` | NEW | ~405 |
| `scripts/vn_invoice_feeder.py` | NEW | ~310 |
| `scripts/smoke_vn_feeder.py` | NEW | ~190 |
| `src/accounting_agent/regulations/__init__.py` | NEW | 1 |
| `src/accounting_agent/regulations/tt133_index.py` | NEW | ~280 |
| `src/accounting_agent/agent_service/main.py` | MODIFIED | +55 (2 endpoints) |
| `src/accounting_agent/ui/app.py` | MODIFIED | +95 (new tab) |
| `openapi/agent-service.yaml` | AUTO | updated |

## 4. Gate Results

| Check | Result |
|-------|--------|
| `ruff check .` | ✅ All checks passed |
| `python3 -m compileall -q src scripts` | ✅ OK |
| `pytest tests/` | ✅ 107 passed, 5 skipped |
| OpenAPI export + diff | ✅ Clean |
| GitHub CI (ci.yml) | ✅ GREEN |
| GitHub Deploy (deploy-staging.yml) | ✅ GREEN |

## 5. Staging Verification

- `GET /agent/v1/healthz` → `{"status":"ok"}`  ✅
- `GET /agent/v1/vn_feeder/status` → HTTP 200 ✅
- `POST /agent/v1/vn_feeder/control` → `{"status":"ok","action":"stop"}` ✅
- UI tab "Command Center" accessible ✅

## 6. Kiến trúc không bị phá vỡ

- Không refactor code cũ
- Chỉ thêm mới: 3 scripts, 1 package `regulations/`, 2 endpoints, 1 UI tab
- Feeder sử dụng `run_type=voucher_ingest` có sẵn — không tạo flow mới
- Mọi upload là **read-only** đối với ERP gốc (sim only)

## 7. Git Log

```
cadeb17 fix: smoke_vn_feeder global scope SyntaxError
33ff011 feat: VN Invoice Data Stream + Command Center + TT133 index
7b5b3ea (previous) fix(ci): clean manual_qa_test lint + conftest ignore
```
