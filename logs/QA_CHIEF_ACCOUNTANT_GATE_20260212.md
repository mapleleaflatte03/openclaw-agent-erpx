# QA Chief Accountant Gate — 2026-02-12 (UTC)

## 1) Kết luận điều hành
- Kết quả gate: **PASS có điều kiện**
- Điểm sẵn sàng nghiệm thu theo góc nhìn Kế Toán Trưởng: **7.6/10**
- Trạng thái P0: **Không còn P0 mở** trong vòng retest này.

Lý do đạt gate:
- Không thể duyệt bút toán khi tài khoản lỗi/undefined ở luồng vận hành chuẩn.
- OCR đã tách rõ dữ liệu nghiệp vụ (`valid`) và dữ liệu bị loại (`quarantined/non_invoice`), không còn trộn trong view mặc định kế toán.
- Q&A đã trả lời đúng kiểu data-driven cho câu hỏi quản trị và hiển thị confidence + tham chiếu.
- Forecast không còn hiển thị `undefined`/0 rác khi thiếu dữ liệu.
- Reconcile/Risk/Reports đã đồng bộ cảnh báo dữ liệu lỗi (không còn `0 ₫` matched, report validation phản ánh chất lượng input).

## 2) Scope và môi trường kiểm thử
- URL: `https://app.welliam.codes/`
- Repo: `/root/accounting-agent-layer`
- Commit vá mới nhất vòng này: `82c206c`
- CI/CD:
  - `ci` run `21956974099`: **success**
  - `deploy-staging` run `21956974107`: **success**
- K8s rollout:
  - `kubectl -n accounting-agent-staging rollout status deployment/agent-service`: **success**
  - Pod mới: `agent-service-65fb467c6d-8kr5r` (`Running`)

## 3) Kết quả theo checklist P0/P1

| Hạng mục | Kết quả | Nhận xét |
|---|---|---|
| 1) Journal: không duyệt TK `undefined` | **PASS** | FE/BE gate hoạt động; proposal lỗi không được approve |
| 2) OCR: chặn rác/0 VND khỏi luồng kế toán | **PASS** | View mặc định chỉ còn chứng từ `valid`; rác nằm ở vùng review/quarantine |
| 3) Q&A: data vs lý thuyết đúng ngữ cảnh | **PASS** | Câu hỏi doanh thu/chi phí trả số liệu thật + reference, không lạc đề TT133 |
| 4) Forecast: không `undefined`/0 vô nghĩa | **PASS** | Hiển thị thông báo thiếu dữ liệu thay cho bảng/chart rác |
| 5) Reconcile–Risk–Reports nhất quán cảnh báo | **PASS** | `0 ₫` không còn matched; risk resolve 200; reports validate phản ánh input quality fail |

## 4) Evidence chi tiết

### 4.1 Journal hard rule (P0)
- UI pending list không còn proposal lỗi trong luồng mặc định:
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-30-49-796Z.yml`
- API xác nhận:
  - `GET /agent/v1/acct/journal_proposals?status=pending&limit=50` -> `invalid_visible=0`
  - `GET /agent/v1/acct/journal_proposals?status=pending&include_invalid=true&limit=50` -> vẫn còn `invalid_total=1` (legacy/source issue)
  - `output/playwright/welliam-review-fix3/api-check-20260212.json`

### 4.2 OCR gating + data quality (P0)
- UI mặc định `Hợp lệ cho hạch toán`:
  - chỉ hiển thị chứng từ `valid`, không trộn `dogs-vs-cats` trong view vận hành
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-30-46-561Z.yml`
- API quality summary:
  - `status_counts = {quarantined: 14, valid: 2, non_invoice: 1}`
  - `operational_total = 2`
  - `output/playwright/welliam-review-fix3/api-check-20260212.json`

### 4.3 Q&A data-driven correctness (P0)
- Câu hỏi: “Doanh thu tháng này là bao nhiêu và 3 khoản chi lớn nhất?”
- Kết quả UI:
  - Trả số liệu doanh thu + top chi phí cụ thể
  - Hiển thị `Độ tin cậy: 90% • 6 nguồn`
  - Có danh sách `Cơ sở tri thức` và `Chứng từ liên quan`
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-31-46-914Z.yml`
- API meta:
  - `route=data`, `confidence=0.904`, `sources_count=6`, `related_vouchers_count=5`
  - `output/playwright/welliam-review-fix3/api-check-20260212.json`

### 4.4 Forecast sufficiency (P1)
- UI không render dữ liệu rác khi thiếu lịch sử:
  - Hiển thị rõ: “Chưa đủ dữ liệu lịch sử để dự báo dòng tiền có ý nghĩa...”
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-35-09-001Z.yml`

### 4.5 Consistency Reconcile–Risk–Reports (P1)
- Reconcile:
  - Không còn dòng `0 ₫` ở trạng thái `✓ matched`; các dòng `0 ₫` hiện `✗`
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-30-53-171Z.yml`
  - API check: `zero_amount_matched = 0`
  - `output/playwright/welliam-review-fix3/api-check-20260212.json`
- Risk:
  - item `open` có nút `✓ Giải quyết`; item resolved hiện `Đã xử lý`
  - resolve gọi API thành công:
    - `POST /agent/v1/acct/anomaly_flags/{id}/resolve -> 200`
    - `output/playwright/welliam-review-fix3/.playwright-cli/network-2026-02-12T17-35-43-934Z.log`
    - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-35-43-429Z.yml`
- Reports:
  - vào tab đã có default type, bấm `🔍 Chạy kiểm tra` lần đầu chạy được
  - checklist chuyển từ `○` sang `✓/✗` và có summary timestamp
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-30-56-354Z.yml`
  - `output/playwright/welliam-review-fix3/.playwright-cli/page-2026-02-12T17-31-10-873Z.yml`

## 5) Kỹ thuật ổn định phiên test
- Network: tất cả request nghiệp vụ ghi nhận trong phiên đều `200` (không có `500`)
  - `output/playwright/welliam-review-fix3/.playwright-cli/network-2026-02-12T17-32-10-015Z.log`
- Console: `Total messages: 0 (Errors: 0, Warnings: 0)`
  - `output/playwright/welliam-review-fix3/.playwright-cli/console-2026-02-12T17-36-07-575Z.log`
- Run engine readiness:
  - `/agent/v1/ray/status` báo `ray_available=false`, nhưng `local_executor_enabled=true`, `celery_worker_count=1`, `run_dispatch_ready=true`
  - run `bank_reconcile` không kẹt queued (`status=success`)
  - `output/playwright/welliam-review-fix3/run-engine-check-20260212.json`

## 6) Tồn đọng và cải tiến bắt buộc sprint kế tiếp (không blocker P0)
1. **Source sanitation journal (P1):** vẫn còn 1 proposal legacy `has_invalid_accounts=true` khi gọi `include_invalid=true`; cần chặn ngay tại nguồn sinh proposal để không tạo record lỗi mới.
2. **Data hygiene lịch sử (P1):** dữ liệu OCR/reconcile cũ dạng noise vẫn xuất hiện trong view “all/review”; cần job cleanup/archive để môi trường vận hành sạch hơn.
3. **Q&A audit depth (P2):** đã có confidence + sources, nhưng nên bổ sung mapping chứng từ dễ đọc hơn (mã CT + link drill-down trực tiếp).

## 7) Kết luận gate
- **P0: Đóng** cho vòng retest này.
- Mức sẵn sàng nghiệm thu Kế Toán Trưởng: **7.6/10 (>=7/10)**.
- Điều kiện để tăng lên >=8.5/10: hoàn tất source sanitation journal + cleanup dữ liệu lịch sử OCR/reconcile.
