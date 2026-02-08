# Tier B Mapping — Hiện Trạng và Kế Hoạch Chỉnh Sửa

> Tài liệu nội bộ mô tả vị trí Tier B trong codebase hiện tại và các điểm cần bổ sung
> để khớp với [DESIGN_PRINCIPLES_TIERED_AGENT.md](DESIGN_PRINCIPLES_TIERED_AGENT.md).

## 1. Tier B hiện tại đang ở đâu?

### Backend — Agent Service (`src/openclaw_agent/agent_service/main.py`)

| Endpoint | Chức năng |
|----------|-----------|
| `GET /agent/v1/contract/cases/{case_id}/obligations` | Trả toàn bộ obligations, sắp xếp theo `created_at DESC`. Model `AgentObligation` đã có trường `confidence` (float) và `risk_level` (high/medium/low). |
| `GET /agent/v1/contract/cases/{case_id}/proposals` | Trả proposals kèm `tier` (int), `confidence`, `risk_level`, `status`, `approvals_required/approved`. |
| `POST /agent/v1/contract/proposals/{id}/approvals` | Duyệt/từ chối proposals — maker-checker đã có (so sánh `created_by` vs `approver_id`). |

### Worker — Pipeline (`src/openclaw_agent/agent_worker/tasks.py`)

- `contract_obligation` run type: trích xuất nghĩa vụ từ PDF/email, gán `confidence` theo rule/pattern.
- Kết quả lưu vào `agent_obligations` với `confidence`, `risk_level`, `signature`.
- Evidence (đoạn trích dẫn) lưu vào `agent_obligation_evidence`.

### UI — Streamlit (`src/openclaw_agent/ui/app.py`)

| Khu vực | Hiện trạng |
|---------|------------|
| Obligations section (line ~160-190) | Hiển thị **tất cả** obligations trong 1 dataframe duy nhất. Không phân tách high-confidence vs candidate. |
| Proposals section (line ~195-270) | Hiển thị proposals + maker-checker logic. Đã có `tier`, `confidence`, nhưng UI chưa dùng để phân nhóm. |
| Disclaimer | **Chưa có.** |
| Feedback | **Chưa có** (không có nút Đúng/Sai, không ghi implicit feedback). |

### Database / Models (`src/openclaw_agent/common/models.py`)

- `AgentObligation`: có `confidence`, `risk_level`, `obligation_type` — đủ để phân nhóm.
- `AgentFeedback`: tồn tại nhưng generic (cho mọi task type), chưa có bảng riêng cho Tier B feedback.
- `AgentAuditLog`: ghi log bất biến các action, nhưng chưa ghi implicit feedback từ hành vi duyệt.

## 2. Maker-checker hiện tại ra sao?

- **Đã implement:** `AgentApproval` + `AgentProposal` với:
  - `_approvals_required(risk_level)` → 2 cho high risk, 1 cho medium/low.
  - UI kiểm tra `maker == current_user` → chặn tự duyệt.
  - `evidence_ack` checkbox bắt buộc trước khi duyệt.
- **Khớp principle:** Nguyên tắc #1 (maker-checker 2 lớp cho rủi ro cao) đã được thể hiện.

## 3. Chỗ nào sẽ chỉnh UI để tách high-confidence vs candidate

### File: `src/openclaw_agent/ui/app.py`

**Vị trí:** Section "Obligations" (hiện tại line ~160-190).

**Thay đổi:**
1. Lấy obligations từ API.
2. Chia thành 2 nhóm dựa trên `confidence`:
   - **High-confidence:** `confidence >= 0.75` — hiển thị đầy đủ (type, amount, due_date, evidence link).
   - **Candidate:** `confidence < 0.75` — hiển thị tối đa 5 mục, sort theo ưu tiên (payment > penalty > discount > khác). Có nút "Xem thêm (n)" nếu còn nhiều hơn.
3. Thêm disclaimer rõ ở đầu section Tier B.

### File: `src/openclaw_agent/agent_service/main.py`

**Không cần sửa endpoint**, UI tự phân nhóm dựa trên field `confidence` đã có.

## 4. Chỗ nào sẽ đặt feedback (nút Đúng/Sai)

### Explicit (micro) feedback

- **Vị trí UI:** Cạnh mỗi obligation trong section Obligations.
- **Backend:** Tạo endpoint `POST /agent/v1/tier-b/feedback` ghi vào bảng mới `tier_b_feedback`.
- **Bảng DB (migration 0005):** `tier_b_feedback` với `obligation_id`, `feedback_type`, `user_id`, `created_at`.

### Implicit feedback

- **Vị trí:** Khi user duyệt/sửa/xóa trong flow approval:
  - Giữ nguyên → `implicit_accept`
  - Chỉnh sửa → `implicit_edit`
  - Xóa/bỏ → `implicit_reject`
- **Logic:** So sánh danh sách obligations ban đầu vs final khi submit.
- **Ghi vào:** Cùng bảng `tier_b_feedback`.

## 5. Drift-alert

- **Script:** `scripts/tier_b_drift_report.py`
- **Logic:** Query `tier_b_feedback` 30 ngày, tính reject rate per `obligation_type`.
- **Ngưỡng:** `reject_rate > 0.2` → in cảnh báo.
- **Chưa cần cron** — chạy thủ công `python scripts/tier_b_drift_report.py --days 30`.
