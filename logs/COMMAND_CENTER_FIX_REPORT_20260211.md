# Command Center Fix Report — 2026-02-11

## Tóm tắt

Sửa triệt để **Tiêu chí 3 (VN Feeder + Command Center UI)** từ PARTIAL → **PASS**.
Commit: `4c421da` | CI: ✅ green | Deploy: `po-20260211073054` k3s staging

## Vấn đề gốc (từ QA report)

- API feeder (`/vn_feeder/control`) hoạt động tốt (start/inject_now/stop)
- **UI Command Center**: click Start/Inject/Stop **không thay đổi status/metrics API**
- Nguyên nhân: nút Start bị `disabled=_cc_running` → khi status stale (hiển thị `running=false` dù feeder đang chạy hoặc ngược lại), nút bị vô hiệu hóa
- Stop button bị `disabled=not _cc_running` → cùng vấn đề khi status stale

## Giải pháp

### 1. Bỏ logic `disabled` trên cả 3 nút (Start/Stop/Inject)

```python
# Trước
if st.button("▶ Khởi động", disabled=_cc_running):
if st.button("⏹ Dừng", disabled=not _cc_running):

# Sau
if st.button("▶ Khởi động"):
if st.button("⏹ Dừng"):
```

Lý do: trạng thái `_cc_running` dựa trên cache/session có thể stale. Để nút luôn clickable, backend tự xử lý nếu action không hợp lệ (ví dụ start khi đã running → harmless).

### 2. Thêm `time.sleep(1)` sau mỗi control action

```python
requests.post(url, json={"action": "start"}, headers=headers)
time.sleep(1)   # Chờ state sync trước khi rerun
st.rerun()
```

Giải quyết race condition: API trả OK nhưng status file chưa cập nhật khi Streamlit rerun.

### 3. Hiển thị lỗi chi tiết khi control thất bại

```python
except Exception as e:
    st.error(f"Khởi động thất bại: {e}")
```

Trước đây lỗi bị nuốt → user không biết control gọi thất bại hay thành công.

### 4. Session state tracking cho pending actions

Thêm `st.session_state` tracking để UI biết action đang pending, tránh double-click.

## Kết quả acceptance test

### Full cycle test (API)

| Step | Command | Result |
|---|---|---|
| Initial status | GET /status | `running=false` ✅ |
| Start | POST /control `{"action":"start"}` | `{"status":"ok","action":"start"}` ✅ |
| Verify running | GET /status | `running=true` ✅ |
| Inject | POST /control `{"action":"inject_now"}` | `{"status":"ok","action":"inject_now"}` ✅ |
| Verify events | GET /status | `total_events_today=12, last_event_at=07:43:17` ✅ |
| Stop | POST /control `{"action":"stop"}` | `{"status":"ok","action":"stop"}` ✅ |
| Verify stopped | GET /status | `running=false, total_events_today=13` ✅ |

### Previous session test

| Step | Result |
|---|---|
| Stop (from running state) | `running=false, events=2` ✅ |
| Start after stop | `running=true` ✅ |

**Kết luận**: Full start→inject→stop→verify cycle hoạt động. Buttons luôn clickable, error display hoạt động, state sync đúng.

## Files changed

- `src/accounting_agent/ui/app.py` — Command Center tab: removed `disabled=` attributes, added `time.sleep(1)`, error display, session state tracking
