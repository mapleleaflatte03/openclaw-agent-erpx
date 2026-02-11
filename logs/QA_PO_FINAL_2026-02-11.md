# QA PO Final Report — 2026-02-11

## Kết luận nhanh
- **PO readiness:** **NO**
- Build hiện tại **chưa nên chốt nghiệm thu cuối** cho hội đồng đề tài vì 4 tiêu chí chưa đạt PASS đồng loạt; các điểm nghẽn chính nằm ở tính ổn định chain run mới và độ quyết định của feeder inject/UI sync.

## Bảng 4 tiêu chí PO
| Tiêu chí | Kết quả | Lý do ngắn |
|---|---|---|
| (1) Q&A kế toán VN (TT200/TT133, VND) | **PARTIAL** | API wiring đúng (`llm_used=true`, không lộ `reasoning_chain`), 9/9 câu trả lời có TK + Nợ/Có + VND; tuy nhiên viện dẫn TT200/TT133 dao động theo lần gọi nên chưa đạt PASS tuyệt đối. |
| (2) Chuỗi ERP mô phỏng | **PARTIAL** | Endpoint chain đọc được dữ liệu; tạo run mới thành công nhưng run mới trong phiên test chủ yếu ở `queued`, chưa chứng minh rõ event mới -> downstream artifact của chính run đó. |
| (3) VN Feeder + Command Center | **PARTIAL** | API Start/Stop hoạt động, metrics/sources có tăng và giữ ổn sau stop; UI hiển thị trạng thái + metrics + nguồn. Inject/sync theo thời điểm vẫn dao động nên chưa đạt PASS chắc chắn. |
| (4) UI Tạo tác vụ + period | **PARTIAL** | UI có field period cho `voucher_ingest`, `soft_checks`, `tax_export`; API validate period đúng 422/422/200. Tuy vậy bằng chứng tự động cho việc run mới luôn hiện ngay trong Manage tab chưa đủ chắc trong vòng test này. |

## Regression
- `ruff check .` → **PASS**
- `pytest tests/ -q` → **PASS** (lần chạy theo đúng lệnh yêu cầu, không fail)
- `python3 scripts/export_openapi.py` → **PASS**
- Health checks (Mode B):
  - `GET /healthz` → **200**
  - `GET /readyz` → **200**
  - `GET /agent/v1/healthz` → **200**
  - `GET /agent/v1/readyz` → **200**
- UI tab scan (Mode A): quét đủ 11 tab chính, **không ghi nhận 500/traceback** trong phiên test; network 5xx = 0, console error = 0.

## Điểm bằng chứng nổi bật
- Q&A API 3 câu x 3 lần đều HTTP 200; `meta.llm_used=true`; không thấy `reasoning_chain` trong response public.
- Ví dụ run ID để PO tái kiểm tra: `892bfa0c-8bf7-404f-ba60-507538612f4c`, `70ebde5a-0c35-4bef-9e43-0c74e5343626`, `ff202d15-6dc3-4f4b-9524-d565cf061e9c` (period `2026-02`).
- Feeder metrics snapshot API:
  - Trước start: `running=false`, `total_events_today=0`
  - Sau start (poll): `running=true`, `total_events_today=4`, `sources=3`
  - Sau stop: `running=false`, `total_events_today` giữ ổn định giữa hai lần đọc liên tiếp

## Top 3 đề xuất cải tiến tiếp theo
1. **P0** — Ổn định thực thi worker/queue để run mới không kẹt `queued` quá lâu; bổ sung watchdog + cảnh báo cho run quá hạn SLA.
2. **P1** — Bắt buộc hậu kiểm format Q&A trước trả về (đảm bảo luôn có viện dẫn TT200/TT133 khi câu hỏi yêu cầu), giảm dao động giữa các lần gọi.
3. **P2** — Cải thiện Command Center: phản hồi hành động `inject_now` theo event-id + refresh trạng thái tức thời để UI/API đồng bộ rõ ràng hơn cho PO demo.
