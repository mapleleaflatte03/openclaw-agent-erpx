# QA PO Final Report — 2026-02-11

## Kết luận ngắn
Build hiện tại **chưa đạt mức sẵn sàng hoàn toàn cho PO/hội đồng** vì còn 2 tiêu chí ở mức PARTIAL (Q&A kế toán và Command Center UI).

## Bảng 4 tiêu chí PO

| Tiêu chí | Kết quả | Lý do |
|---|---|---|
| (1) Q&A kế toán VN (TT200/TT133) | **PARTIAL** | Wiring đạt (llm_used=true, không lộ reasoning_chain) nhưng dao động mạnh: có lượt chi tiết, có lượt fallback/generic và có câu trả lời tiếng Anh/nháp. |
| (2) Chuỗi ERP mô phỏng | **PASS** | Có run soft_checks mới `a82dc716-cfd5-40da-98c5-e1ddd6839a3d` trạng thái success và truy vết downstream được ở `/agent/v1/acct/soft_check_results` (run_id trùng khớp). |
| (3) VN Feeder + Command Center | **PARTIAL** | API start/inject/stop hoạt động và metrics tăng; nhưng thao tác Start/Inject/Stop trên UI không làm status/metrics API thay đổi trong phiên test. |
| (4) UI tạo tác vụ + period | **PASS** | UI có field period cho voucher_ingest/soft_checks/tax_export; API trả 422 đúng cho thiếu/sai period và tạo run thành công khi period hợp lệ. |

## Regression

| Hạng mục | Kết quả | Ghi chú |
|---|---|---|
| `ruff check .` | **PASS** | ruff check . -> exit 0 (All checks passed!) |
| `pytest tests/ -q` | **PASS** | pytest tests/ -q -> exit 0 (warnings deprecation, không có test fail) |
| `python3 scripts/export_openapi.py` | **PASS** | python3 scripts/export_openapi.py -> exit 0; openapi/agent-service.yaml và openapi/erpx-mock.yaml được cập nhật. |
| Health endpoints | **PASS** | /healthz, /readyz, /agent/v1/healthz, /agent/v1/readyz đều HTTP 200. |
| Quét tab UI chính | **PASS** | Quét đủ tab chính trong session, không ghi nhận traceback/500 ở snapshot và console warning/error = 0. |

## Bằng chứng nổi bật

- Run soft-check mới qua UI: `a82dc716-cfd5-40da-98c5-e1ddd6839a3d` -> `status=success`, task chain `pull_delta/checks/export_report/acct_soft_checks` đều success.
- Downstream trace: `/agent/v1/acct/soft_check_results` có bản ghi `5dd62027-e743-48d6-9874-933939a8b882` với `run_id=a82dc716-cfd5-40da-98c5-e1ddd6839a3d` và `period=2026-02`.
- Case period API: thiếu period -> 422; period `2026-13` -> 422; period `2026-02` -> 200 (`run_id=b7069b0d-cfd3-461d-9a8a-eb8785944503`).
- Feeder API: `start` làm `running=true` và metrics tăng; `inject_now` tăng ngay; `stop` trả `running=false`.
- UI Command Center: click Start/Inject/Stop trong phiên test không làm metrics API thay đổi (status giữ `running=false`).

## Code Audit Notes (nguyên nhân khả dĩ)

- Q&A dao động/fallback: `src/openclaw_agent/flows/qna_accounting.py:103`, `src/openclaw_agent/flows/qna_accounting.py:648`, `src/openclaw_agent/flows/qna_accounting.py:662` có cơ chế lọc mạnh + fallback context, dễ rơi vào trả lời generic khi output LLM nhiễu.
- API run idempotency: `src/openclaw_agent/agent_service/main.py:422` tự sinh idempotency từ payload nếu client không truyền key, dẫn tới gọi lặp cùng payload nhận lại run cũ.
- Mismatch trạng thái UI/worker: worker set trạng thái thành `success` ở `src/openclaw_agent/agent_worker/tasks.py:428`, trong khi UI label ưu tiên `completed/failed/running/queued` tại `src/openclaw_agent/ui/app.py:50` (dễ hiển thị `❓`).
- Command Center control nằm ở `src/openclaw_agent/ui/app.py:1436`–`src/openclaw_agent/ui/app.py:1458`; trong test UI thao tác click không phản ánh xuống API status, cần điều tra thêm luồng request/rerun trên Streamlit staging.

## Top 3 đề xuất cải tiến tiếp theo

1. **P0**: Khóa chất lượng Q&A cho bộ câu chuẩn PO (template/guardrail deterministic cho TK, Nợ/Có, VND, TT200/TT133; chặn output English/nháp trước khi trả về UI/API).
2. **P1**: Sửa độ tin cậy Command Center UI (xác nhận event click thực sự gọi `/vn_feeder/control`, refresh trạng thái sau action, và hiển thị lỗi nếu control thất bại).
3. **P2**: Chuẩn hóa lifecycle run + monitoring queue (đồng nhất `success/completed`, cảnh báo backlog run `queued` lâu, và hiển thị run log rõ hơn trên tab Quản lý tác vụ).