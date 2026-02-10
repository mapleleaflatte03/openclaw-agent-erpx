from __future__ import annotations

import contextlib
import os
import re
import time
from datetime import date
from typing import Any

import boto3
import pandas as pd
import requests
import streamlit as st

AGENT_BASE_URL = os.getenv("UI_AGENT_BASE_URL", "http://localhost:8000").rstrip("/")
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
DEBUG_UI = os.getenv("DEBUG_UI", "").lower() in ("1", "true", "yes")

MINIO_ENDPOINT = os.getenv("UI_MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("UI_MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("UI_MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET_DROP = os.getenv("UI_MINIO_BUCKET_DROP", os.getenv("MINIO_BUCKET_DROP", "agent-drop"))

# Auto-refresh interval (seconds) — set to 0 to disable
_AUTO_REFRESH_SECONDS = int(os.getenv("UI_AUTO_REFRESH_SECONDS", "15"))

# ---------------------------------------------------------------------------
# Vietnamese labels for run_types
# ---------------------------------------------------------------------------
_RUN_TYPE_LABELS: dict[str, str] = {
    "journal_suggestion": "Đề xuất bút toán",
    "bank_reconcile": "Đối chiếu ngân hàng",
    "cashflow_forecast": "Dự báo dòng tiền",
    "voucher_ingest": "Nhập chứng từ",
    "voucher_classify": "Phân loại chứng từ",
    "tax_export": "Xuất báo cáo thuế",
    "working_papers": "Bảng tính kiểm toán",
    "soft_checks": "Kiểm tra logic",
    "ar_dunning": "Nhắc nợ công nợ",
    "close_checklist": "Danh mục kết kỳ",
    "evidence_pack": "Gói bằng chứng",
    "kb_index": "Cập nhật kho tri thức",
    "contract_obligation": "Nghĩa vụ hợp đồng",
}

_RUN_TYPE_ORDER = list(_RUN_TYPE_LABELS.keys())

# Status labels in Vietnamese
_STATUS_LABELS: dict[str, str] = {
    "queued": "⏳ Đang chờ",
    "running": "🔄 Đang chạy",
    "completed": "✅ Hoàn thành",
    "failed": "❌ Thất bại",
    "pending": "⏳ Chờ duyệt",
    "approved": "✅ Đã duyệt",
    "rejected": "❌ Đã từ chối",
    "open": "🔵 Chưa xử lý",
    "resolved": "✅ Đã xử lý",
    "ignored": "⏭️ Bỏ qua",
}

# Severity labels in Vietnamese
_SEVERITY_LABELS: dict[str, str] = {
    "critical": "🔴 Nghiêm trọng",
    "high": "🟠 Cao",
    "medium": "🟡 Trung bình",
    "low": "🟢 Thấp",
    "error": "🔴 Lỗi",
    "warning": "🟡 Cảnh báo",
    "info": "🔵 Thông tin",
}

# P0 security: current_user_id from env, not editable by user
_DEMO_USER_ID = os.getenv("OPENCLAW_DEMO_USER_ID", "demo-checker")


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if AGENT_API_KEY:
        h["X-API-Key"] = AGENT_API_KEY
    return h


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        r = requests.get(f"{AGENT_BASE_URL}{path}", params=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        detail = ""
        with contextlib.suppress(Exception):
            detail = e.response.json().get("detail", "")
        raise RuntimeError(detail or f"Lỗi {e.response.status_code} khi tải dữ liệu.") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError("Không thể kết nối API backend. Vui lòng kiểm tra hệ thống.") from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError("API backend phản hồi quá chậm (timeout). Thử lại sau.") from e


def _post(path: str, json_body: dict[str, Any], idem: str | None = None) -> Any:
    headers = {"Content-Type": "application/json", **_headers()}
    if idem:
        headers["Idempotency-Key"] = idem
    try:
        r = requests.post(f"{AGENT_BASE_URL}{path}", json=json_body, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        detail = ""
        with contextlib.suppress(Exception):
            detail = e.response.json().get("detail", "")
        raise RuntimeError(detail or f"Lỗi {e.response.status_code} khi gửi yêu cầu.") from e
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError("Không thể kết nối API backend. Vui lòng kiểm tra hệ thống.") from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError("API backend phản hồi quá chậm (timeout). Thử lại sau.") from e


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=os.getenv("MINIO_REGION", "sgp1"),
    )


def _validate_period(period: str) -> bool:
    """Validate period format YYYY-MM."""
    return bool(re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period.strip()))


def _action_guard(key: str) -> bool:
    """Double-click guard: returns True if action was already done."""
    return bool(st.session_state.get(f"_guard_{key}"))


def _mark_done(key: str) -> None:
    """Mark action as done for double-click guard."""
    st.session_state[f"_guard_{key}"] = True


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="ERP-X AI Kế toán – OpenClaw Agent", layout="wide")

# CSS: DataFrame toolbar fix + agent-feel styling + hex icon
st.markdown(
    """
    <style>
    [data-testid="stDataFrame"] [data-testid="stElementToolbar"] {
        z-index: 100 !important;
        pointer-events: auto !important;
    }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    .agent-status { animation: pulse 2s infinite; }
    /* Hexagon agent icon — top-right corner */
    .hex-badge {
        position: fixed; top: 12px; right: 18px; z-index: 9999;
        width: 50px; height: 50px; cursor: pointer;
        background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%);
        clip-path: polygon(50% 0%, 93% 25%, 93% 75%, 50% 100%, 7% 75%, 7% 25%);
        display: flex; align-items: center; justify-content: center;
        color: white; font-size: 22px; font-weight: bold;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        transition: transform 0.2s ease;
    }
    .hex-badge:hover { transform: scale(1.15); }
    .timeline-step { border-left: 3px solid #1a73e8; padding: 4px 0 4px 14px; margin-left: 14px; }
    .timeline-step.completed { border-color: #34a853; }
    .timeline-step.failed { border-color: #ea4335; }
    .timeline-step.running { border-color: #fbbc04; }
    </style>
    <!-- Hexagonal Agent Icon — click scrolls to Agent Command Center tab -->
    <div class="hex-badge" title="Trung tâm điều khiển Agent">🤖</div>
    """,
    unsafe_allow_html=True,
)

st.title("🤖 ERP-X AI Kế toán — Agent")
st.caption("OpenClaw Agent — Trợ lý kế toán thông minh tự hành (chỉ đọc — không ghi vào ERP gốc)")
if DEBUG_UI:
    with st.expander("⚙️ Phát triển / Gỡ lỗi", expanded=False):
        st.caption(f"Agent API: {AGENT_BASE_URL}")

# Auto-refresh state
if _AUTO_REFRESH_SECONDS > 0:
    _auto_key = "_last_auto_refresh"
    _now = time.time()
    _last = st.session_state.get(_auto_key, 0.0)
    if _now - _last >= _AUTO_REFRESH_SECONDS:
        st.session_state[_auto_key] = _now
        st.rerun()

current_user = _DEMO_USER_ID

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
(
    tab_agent,
    tab_trigger,
    tab_runs,
    tab_journal,
    tab_anomaly,
    tab_check,
    tab_cashflow,
    tab_voucher,
    tab_qna,
    tab_contract,
) = st.tabs([
    "🤖 Trung tâm điều khiển",
    "📋 Tạo tác vụ",
    "📂 Quản lý tác vụ",
    "🧾 Bút toán đề xuất",
    "🔍 Giao dịch bất thường",
    "📊 Kiểm tra & Báo cáo",
    "💰 Dòng tiền",
    "📥 Chứng từ",
    "💬 Hỏi đáp",
    "🔬 Hợp đồng (Thử nghiệm)",
])


# ===== TAB 0: Trung tâm điều khiển Agent ==============================
with tab_agent:
    st.subheader("🤖 Trung tâm điều khiển Agent")
    st.markdown(
        "**Điều khiển Agent bằng mục tiêu** — nhập lệnh tiếng Việt, "
        "Agent tự điều phối chuỗi tác vụ phù hợp."
    )
    # Agent status badge
    st.markdown(
        '<span style="background:#34a853;color:#fff;padding:2px 10px;'
        'border-radius:12px;font-size:0.85em;">● Trực tuyến</span> '
        '<span style="color:#999;font-size:0.8em;">v1.0 — 10 nhóm nghiệp vụ</span>',
        unsafe_allow_html=True,
    )
    st.markdown("")

    # --- Goal-centric command input (CLI-style) ---
    col_cmd, col_period = st.columns([3, 1])
    with col_cmd:
        agent_command = st.text_input(
            "🎯 Nhập lệnh cho Agent",
            value="",
            placeholder='Ví dụ: "Đóng sổ tháng 1/2026" hoặc "Kiểm tra kỳ 2026-01"',
            key="agent_cmd_input",
        )
    with col_period:
        agent_period = st.text_input(
            "Kỳ (YYYY-MM)",
            value=date.today().strftime("%Y-%m"),
            key="agent_cmd_period",
        )

    # Available goals — CLI-style skill list
    with st.expander("📋 Các lệnh mà Agent hiểu (nhấn để xem)", expanded=False):
        st.markdown("""
| Lệnh | Chuỗi tác vụ Agent sẽ thực hiện | Số bước |
|---|---|---|
| **Đóng sổ tháng X** | Nhập CT → Phân loại → Bút toán → Đối chiếu → Kiểm tra → Thuế → Dòng tiền | 7 |
| **Kiểm tra kỳ X** | Nhập CT → Phân loại → Kiểm tra logic | 3 |
| **Đối chiếu ngân hàng** | Đối chiếu NH → Kiểm tra logic | 2 |
| **Báo cáo thuế tháng X** | Nhập CT → Phân loại → Bút toán → Xuất báo cáo thuế | 4 |
| **Nhập chứng từ** | Nhập CT → Phân loại | 2 |
| **Dự báo dòng tiền** | Dự báo dòng tiền | 1 |
| **Phát hiện bất thường** | Kiểm tra logic → Phát hiện anomaly | 2 |
| **Rà soát hợp đồng** | Nghĩa vụ hợp đồng | 1 |

> 💡 **Mẹo:** Bạn có thể nhập lệnh tự do — Agent sẽ cố gắng hiểu và chọn chuỗi tác vụ phù hợp nhất.
        """)

    if st.button("🚀 Gửi lệnh cho Agent", key="agent_cmd_go", type="primary"):
        if not agent_command.strip():
            st.warning("⚠️ Vui lòng nhập lệnh cho Agent.")
        elif agent_period.strip() and not _validate_period(agent_period.strip()):
            st.error("❌ Kỳ kế toán không đúng định dạng. Vui lòng nhập theo YYYY-MM (ví dụ: 2026-01).")
        else:
            with st.spinner("🤖 Agent đang phân tích lệnh và điều phối tác vụ…"):
                try:
                    cmd_res = _post(
                        "/agent/v1/agent/commands",
                        {
                            "command": agent_command.strip(),
                            "period": agent_period.strip() or None,
                        },
                    )
                    if cmd_res.get("status") == "no_chain":
                        st.warning(
                            f"⚠️ {cmd_res.get('message', 'Không nhận diện được mục tiêu.')}\n\n"
                            f"**Gợi ý:** {', '.join(cmd_res.get('available_goals', []))}"
                        )
                    else:
                        runs = cmd_res.get("runs", [])
                        chain = cmd_res.get("chain", [])
                        st.success(
                            f"✅ Agent đã tiếp nhận lệnh: **{cmd_res.get('goal_label', '')}**\n\n"
                            f"📊 Chuỗi tác vụ: {len(chain)} bước  •  "
                            f"Tác vụ tạo mới: {sum(1 for r in runs if not r.get('reused'))}"
                        )
                        for r in runs:
                            icon = "♻️" if r.get("reused") else "🆕"
                            st.caption(
                                f"  {icon} {_RUN_TYPE_LABELS.get(r['run_type'], r['run_type'])} "
                                f"— `{r['run_id'][:12]}…` [{_STATUS_LABELS.get(r['status'], r['status'])}]"
                            )
                        time.sleep(0.5)
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Lỗi gửi lệnh: {e}")

    st.divider()

    # --- Activity Timeline ---
    st.subheader("📜 Dòng thời gian hoạt động Agent")
    col_tl_hdr, col_tl_ref = st.columns([3, 1])
    with col_tl_ref:
        if st.button("🔄 Làm mới", key="refresh_timeline"):
            st.rerun()

    try:
        timeline = _get("/agent/v1/agent/timeline", params={"limit": 30})
        tl_items = timeline.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải dòng thời gian: {e}")
        tl_items = []

    if tl_items:
        for item in tl_items:
            icon = item.get("icon", "❓")
            title = item.get("title", "")
            detail = item.get("detail", "")
            ts = item.get("ts", "")[:19]
            item_type = item.get("type", "run")
            status = item.get("status", "")

            css_class = "completed" if status == "completed" else (
                "failed" if status == "failed" else (
                    "running" if status == "running" else ""
                )
            )

            if item_type == "run":
                st.markdown(
                    f'<div class="timeline-step {css_class}">'
                    f"<strong>{icon} {title}</strong><br/>"
                    f"<small>🕐 {ts} — {detail}</small></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="timeline-step {css_class}" style="margin-left: 30px;">'
                    f"{icon} {title}<br/>"
                    f"<small>{detail}</small></div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info(
            "Chưa có hoạt động nào. Gửi lệnh cho Agent ở trên hoặc "
            "tạo tác vụ ở tab **📋 Tạo tác vụ** để bắt đầu!"
        )


# ===== TAB 1: Tạo tác vụ =============================================
with tab_trigger:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Tạo tác vụ thủ công")
        requested_by = st.text_input("Người yêu cầu (tùy chọn)", value="", key="trig_user")
        run_type = st.selectbox(
            "Loại tác vụ",
            _RUN_TYPE_ORDER,
            format_func=lambda rt: _RUN_TYPE_LABELS.get(rt, rt),
            key="trig_rt",
        )
        payload: dict[str, Any] = {}
        _period_required = run_type in {"tax_export", "working_papers", "close_checklist"}
        if run_type in {"tax_export", "working_papers", "close_checklist"}:
            payload["period"] = st.text_input(
                "Kỳ kế toán (YYYY-MM) *", value=date.today().strftime("%Y-%m"), key="trig_period",
            )
        if run_type == "soft_checks":
            payload["updated_after"] = st.text_input("Cập nhật sau (ISO)", value="", key="trig_ua")
            payload["period"] = st.text_input(
                "Kỳ kế toán (YYYY-MM, tùy chọn)", value=date.today().strftime("%Y-%m"), key="trig_sc_period",
            )
        if run_type == "cashflow_forecast":
            payload["period"] = st.text_input(
                "Kỳ (YYYY-MM)", value=date.today().strftime("%Y-%m"), key="trig_cf_period",
            )
            payload["horizon_days"] = st.number_input("Số ngày dự báo", min_value=7, max_value=90, value=30)
        if run_type == "voucher_ingest":
            payload["source"] = st.selectbox("Nguồn dữ liệu", ["vn_fixtures", "payload", "erpx_mock"])
        if run_type == "voucher_classify":
            payload["period"] = st.text_input("Kỳ (YYYY-MM, tùy chọn)", value="", key="trig_vc_period")
        if run_type == "ar_dunning":
            payload["as_of"] = st.text_input("Ngày cắt (YYYY-MM-DD)", value=date.today().isoformat())
        if run_type == "evidence_pack":
            payload["exception_id"] = st.text_input("Mã ngoại lệ (exception_id)", value="")
            payload["issue_id"] = st.text_input("Mã vấn đề (tùy chọn)", value="")
        if run_type == "kb_index":
            payload["file_uri"] = st.text_input("Đường dẫn file", value="")
            payload["title"] = st.text_input("Tiêu đề (tùy chọn)", value="")
            payload["doc_type"] = st.selectbox("Loại tài liệu", ["process", "law", "template"])
            payload["version"] = st.text_input("Phiên bản", value="v1")
        if run_type == "contract_obligation":
            payload["case_key"] = st.text_input("Mã hợp đồng (case_key, tùy chọn)", value="")
            payload["partner_name"] = st.text_input("Tên đối tác (tùy chọn)", value="")
            payload["partner_tax_id"] = st.text_input("MST đối tác (tùy chọn)", value="")
            payload["contract_code"] = st.text_input("Mã hợp đồng (tùy chọn)", value="")
            payload["contract_files"] = [
                x.strip()
                for x in st.text_area("Danh sách file hợp đồng (mỗi dòng một file)").splitlines()
                if x.strip()
            ]
            payload["email_files"] = [
                x.strip()
                for x in st.text_area("Danh sách file email (mỗi dòng một file)").splitlines()
                if x.strip()
            ]

        idem = st.text_input("Khóa duy nhất (Idempotency-Key, tùy chọn)", value="", key="trig_idem")

        if st.button("▶️ Chạy tác vụ", key="trig_run"):
            _p = (payload.get("period") or "").strip()
            if _period_required and not _p:
                st.error("❌ Vui lòng nhập kỳ kế toán (period) — trường bắt buộc cho loại tác vụ này.")
            elif _p and not _validate_period(_p):
                st.error("❌ Kỳ kế toán không đúng định dạng. Vui lòng nhập theo YYYY-MM (ví dụ: 2026-01).")
            else:
                body: dict[str, Any] = {"run_type": run_type, "trigger_type": "manual", "payload": payload}
                if requested_by.strip():
                    body["requested_by"] = requested_by.strip()
                try:
                    res = _post("/agent/v1/runs", body, idem or None)
                    st.success(
                        f"✅ Tác vụ **{_RUN_TYPE_LABELS.get(run_type, run_type)}** đã được tạo thành công!\n\n"
                        f"Mã tác vụ: `{res.get('run_id', '')}`  •  "
                        f"Trạng thái: {_STATUS_LABELS.get(res.get('status', ''), res.get('status', ''))}"
                    )
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Không thể tạo tác vụ: {e}")

    with col2:
        st.subheader("Tải file lên (Kích hoạt sự kiện)")
        mode = st.selectbox(
            "Loại file", ["attachments", "kb"], key="drop_mode",
            format_func=lambda m: "Chứng từ đính kèm" if m == "attachments" else "Tài liệu tri thức",
        )
        up = st.file_uploader("Chọn file", type=None, key="drop_file")
        if up is not None and st.button("📤 Tải lên", key="drop_upload"):
            key = f"drop/{mode}/{int(time.time())}_{up.name}"
            s3 = _s3()
            s3.put_object(Bucket=MINIO_BUCKET_DROP, Key=key, Body=up.getvalue())
            st.success(f"✅ Đã tải lên thành công: **{up.name}**")


# ===== TAB 2: Quản lý tác vụ ===========================================
with tab_runs:
    col_runs_hdr, col_refresh = st.columns([3, 1])
    with col_runs_hdr:
        st.subheader("Danh sách tác vụ")
    with col_refresh:
        if st.button("🔄 Làm mới", key="refresh_runs"):
            st.rerun()

    col_flt_rt, col_flt_st = st.columns(2)
    with col_flt_rt:
        _filter_rt = st.selectbox(
            "Lọc loại tác vụ",
            ["(tất cả)"] + _RUN_TYPE_ORDER,
            format_func=lambda rt: _RUN_TYPE_LABELS.get(rt, rt) if rt != "(tất cả)" else "(Tất cả)",
            key="run_flt_rt",
        )
    with col_flt_st:
        _filter_st = st.selectbox(
            "Lọc trạng thái",
            ["(tất cả)", "queued", "running", "completed", "failed"],
            format_func=lambda s: _STATUS_LABELS.get(s, s) if s != "(tất cả)" else "(Tất cả)",
            key="run_flt_st",
        )

    try:
        _rp: dict[str, Any] = {"limit": 50}
        if _filter_rt != "(tất cả)":
            _rp["run_type"] = _filter_rt
        if _filter_st != "(tất cả)":
            _rp["status"] = _filter_st
        runs = _get("/agent/v1/runs", params=_rp).get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải danh sách tác vụ: {e}")
        runs = []
    if runs:
        df = pd.DataFrame(runs)
        df["Loại tác vụ"] = df["run_type"].map(lambda rt: _RUN_TYPE_LABELS.get(rt, rt))
        df["Trạng thái"] = df["status"].map(lambda s: _STATUS_LABELS.get(s, s))
        st.dataframe(
            df[["run_id", "Loại tác vụ", "Trạng thái", "trigger_type", "created_at"]],
            use_container_width=True,
            column_config={
                "run_id": "Mã tác vụ",
                "trigger_type": "Nguồn kích hoạt",
                "created_at": "Thời gian tạo",
            },
        )
        run_id = st.text_input("Mã tác vụ xem chi tiết", value=df.iloc[0]["run_id"], key="runs_inspect")

        if run_id:
            colA, colB = st.columns(2)
            with colA:
                st.markdown("### Bước xử lý")
                try:
                    tasks = _get("/agent/v1/tasks", params={"run_id": run_id}).get("items", [])
                except Exception as e:
                    st.error(f"Lỗi tải bước xử lý: {e}")
                    tasks = []
                if tasks:
                    df_t = pd.DataFrame(tasks)
                    df_t["Trạng thái"] = df_t["status"].map(lambda s: _STATUS_LABELS.get(s, s))
                    st.dataframe(
                        df_t[["task_name", "Trạng thái", "error", "created_at"]],
                        use_container_width=True,
                        column_config={
                            "task_name": "Bước",
                            "error": "Lỗi",
                            "created_at": "Thời gian",
                        },
                    )
                else:
                    st.info("Chưa có bước xử lý cho tác vụ này.")
            with colB:
                st.markdown("### Nhật ký hoạt động")
                try:
                    logs = _get("/agent/v1/logs", params={"run_id": run_id, "limit": 200}).get("items", [])
                except Exception as e:
                    st.error(f"Lỗi tải nhật ký: {e}")
                    logs = []
                if logs:
                    st.dataframe(
                        pd.DataFrame(logs)[["ts", "level", "message"]],
                        use_container_width=True,
                        column_config={
                            "ts": "Thời gian",
                            "level": "Mức",
                            "message": "Nội dung",
                        },
                    )
                else:
                    st.info("Chưa có nhật ký cho tác vụ này.")
    else:
        st.info("Chưa có tác vụ nào. Tạo mới ở tab **📋 Tạo tác vụ**.")


# ===== TAB 3: Bút toán đề xuất ========================================
with tab_journal:
    col_jp_hdr, col_jp_ref = st.columns([3, 1])
    with col_jp_hdr:
        st.subheader("🧾 Bút toán đề xuất")
    with col_jp_ref:
        if st.button("🔄 Làm mới", key="refresh_journal"):
            st.rerun()

    st.markdown(f"👤 Người duyệt hiện tại: **{current_user}**")

    try:
        proposals_data = _get("/agent/v1/acct/journal_proposals", params={"limit": 50})
        proposals_acct = proposals_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải bút toán đề xuất: {e}")
        proposals_acct = []

    if proposals_acct:
        for p in proposals_acct:
            lines_str = " | ".join(
                f"{'Nợ' if ln.get('debit', 0) > 0 else 'Có'} TK {ln.get('account_code', '')} "
                f"({ln.get('account_name', '')}) {ln.get('debit', 0) or ln.get('credit', 0):,.0f}"
                for ln in p.get("lines", [])
            )
            status_icon = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(p.get("status", ""), "❓")
            col_p1, col_p2 = st.columns([3, 1])
            with col_p1:
                st.markdown(
                    f"**{status_icon} {p.get('description', 'Không có mô tả')}** — "
                    f"Độ tin cậy: {p.get('confidence', 0):.0%}  \n"
                    f"📝 {lines_str}"
                )
            with col_p2:
                if p.get("status") == "pending":
                    _gk_a = f"jp_approve_{p['id']}"
                    _gk_r = f"jp_reject_{p['id']}"
                    if _action_guard(_gk_a) or _action_guard(_gk_r):
                        st.caption("✔ Đã xử lý — đang làm mới…")
                    else:
                        col_a, col_r = st.columns(2)
                        with col_a:
                            if st.button("✅ Duyệt", key=_gk_a):
                                try:
                                    _post(
                                        f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                        {"status": "approved", "reviewed_by": current_user},
                                    )
                                    _mark_done(_gk_a)
                                    st.success("✅ Đã duyệt bút toán")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"❌ {ex}")
                        with col_r:
                            if st.button("❌ Từ chối", key=_gk_r):
                                try:
                                    _post(
                                        f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                        {"status": "rejected", "reviewed_by": current_user},
                                    )
                                    _mark_done(_gk_r)
                                    st.success("❌ Đã từ chối bút toán")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"❌ {ex}")
                else:
                    st.caption(
                        f"{_STATUS_LABELS.get(p.get('status', ''), p.get('status', ''))} "
                        f"bởi {p.get('reviewed_by', 'N/A')}"
                    )
    else:
        st.info("Chưa có bút toán đề xuất. Chạy **Đề xuất bút toán** ở tab Tạo tác vụ.")


# ===== TAB 4: Giao dịch bất thường ====================================
with tab_anomaly:
    col_an_hdr, col_an_ref = st.columns([3, 1])
    with col_an_hdr:
        st.subheader("🔍 Giao dịch bất thường")
    with col_an_ref:
        if st.button("🔄 Làm mới", key="refresh_anomaly"):
            st.rerun()

    try:
        anomalies_data = _get("/agent/v1/acct/anomaly_flags", params={"limit": 50})
        anomalies = anomalies_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải dữ liệu giao dịch bất thường: {e}")
        anomalies = []

    if anomalies:
        df_anom = pd.DataFrame(anomalies)
        df_anom["Mức độ"] = df_anom["severity"].map(
            lambda s: _SEVERITY_LABELS.get(s, f"⚪ {s}")
        )
        st.dataframe(
            df_anom[["Mức độ", "anomaly_type", "description", "resolution", "created_at"]],
            use_container_width=True,
            column_config={
                "anomaly_type": "Loại bất thường",
                "description": "Mô tả",
                "resolution": "Trạng thái",
                "created_at": "Thời gian",
            },
        )

        open_flags = [a for a in anomalies if a.get("resolution") == "open"]
        if open_flags:
            flag_id = st.selectbox(
                "Chọn giao dịch bất thường cần xử lý",
                [f["id"] for f in open_flags],
                format_func=lambda fid: next(
                    (f"{f['anomaly_type']}: {f['description'][:60]}..." for f in open_flags if f["id"] == fid),
                    fid,
                ),
                key="an_select",
            )
            _gk_res = f"an_resolve_{flag_id}"
            _gk_ign = f"an_ignore_{flag_id}"
            if _action_guard(_gk_res) or _action_guard(_gk_ign):
                st.caption("✔ Đã xử lý — đang làm mới…")
            else:
                col_res, col_ign = st.columns(2)
                with col_res:
                    if st.button("✅ Đã xử lý", key=_gk_res):
                        try:
                            _post(
                                f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                                {"resolution": "resolved", "resolved_by": current_user},
                            )
                            _mark_done(_gk_res)
                            st.success("✅ Đã giải quyết")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"❌ {ex}")
                with col_ign:
                    if st.button("⏭️ Bỏ qua", key=_gk_ign):
                        try:
                            _post(
                                f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                                {"resolution": "ignored", "resolved_by": current_user},
                            )
                            _mark_done(_gk_ign)
                            st.success("⏭️ Đã bỏ qua")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"❌ {ex}")
        else:
            st.success("Không có giao dịch bất thường chưa xử lý. 🎉")
    else:
        st.info("Chưa phát hiện giao dịch bất thường. Chạy **Đối chiếu ngân hàng** ở tab Tạo tác vụ.")


# ===== TAB 5: Kiểm tra & Báo cáo ======================================
with tab_check:
    col_ck_hdr, col_ck_ref = st.columns([3, 1])
    with col_ck_hdr:
        st.subheader("📊 Kiểm tra logic")
    with col_ck_ref:
        if st.button("🔄 Làm mới", key="refresh_check"):
            st.rerun()

    try:
        scr_data = _get("/agent/v1/acct/soft_check_results", params={"limit": 50})
        scr_items = scr_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải kết quả kiểm tra: {e}")
        scr_items = []

    if scr_items:
        df_scr = pd.DataFrame(scr_items)
        df_scr["Điểm"] = df_scr["score"].map(
            lambda s: f"{'🟢' if s >= 0.8 else '🟡' if s >= 0.5 else '🔴'} {s:.0%}"
        )
        st.dataframe(
            df_scr[["period", "total_checks", "passed", "warnings", "errors", "Điểm", "created_at"]],
            use_container_width=True,
            column_config={
                "period": "Kỳ kế toán",
                "total_checks": "Tổng kiểm tra",
                "passed": "Đạt",
                "warnings": "Cảnh báo",
                "errors": "Lỗi",
                "created_at": "Thời gian",
            },
        )
    else:
        # P0: diagnostic info when runs complete but no results
        try:
            recent = _get("/agent/v1/runs", params={"run_type": "soft_checks", "limit": 1})
            ri = recent.get("items", [])
            if ri and ri[0].get("status") == "completed":
                st.info(
                    "Tác vụ **Kiểm tra logic** đã chạy xong nhưng không tạo kết quả — "
                    "có thể chưa có chứng từ trong kỳ hoặc dữ liệu mirror Acct* trống.\n\n"
                    f"Mã tác vụ gần nhất: `{ri[0].get('run_id', '')[:12]}…`"
                )
            else:
                st.info("Chưa có kết quả kiểm tra. Chạy **Kiểm tra logic** ở tab Tạo tác vụ để phân tích dữ liệu.")
        except Exception:
            st.info("Chưa có kết quả kiểm tra. Chạy **Kiểm tra logic** ở tab Tạo tác vụ để phân tích dữ liệu.")

    with st.expander("🔎 Chi tiết — Vấn đề phát hiện", expanded=bool(scr_items)):
        issue_filter = st.selectbox(
            "Lọc trạng thái",
            ["open", "resolved", "ignored", "(tất cả)"],
            format_func=lambda s: _STATUS_LABELS.get(s, s) if s != "(tất cả)" else "(Tất cả)",
            key="vi_filter",
        )
        try:
            vi_params: dict[str, Any] = {"limit": 50}
            if issue_filter != "(tất cả)":
                vi_params["resolution"] = issue_filter
            vi_data = _get("/agent/v1/acct/validation_issues", params=vi_params)
            vi_items = vi_data.get("items", [])
        except Exception as e:
            st.error(f"Lỗi tải vấn đề kiểm tra: {e}")
            vi_items = []

        if vi_items:
            df_vi = pd.DataFrame(vi_items)
            df_vi["Mức độ"] = df_vi["severity"].map(
                lambda sv: _SEVERITY_LABELS.get(sv, f"⚪ {sv}")
            )
            st.dataframe(
                df_vi[["rule_code", "Mức độ", "message", "erp_ref", "resolution", "created_at"]],
                use_container_width=True,
                column_config={
                    "rule_code": "Mã quy tắc",
                    "message": "Nội dung",
                    "erp_ref": "Tham chiếu ERP",
                    "resolution": "Trạng thái",
                    "created_at": "Thời gian",
                },
            )

            resolve_id = st.text_input("Mã vấn đề (Issue ID) để xử lý", value="", key="resolve_vi_id")
            if resolve_id:
                _gk_vi = f"vi_resolve_{resolve_id}"
                if _action_guard(_gk_vi):
                    st.caption("✔ Đã xử lý")
                elif st.button("✅ Đánh dấu đã xử lý", key="resolve_vi_btn"):
                    try:
                        _post(
                            f"/agent/v1/acct/validation_issues/{resolve_id}/resolve",
                            {"action": "resolved", "resolved_by": current_user},
                        )
                        _mark_done(_gk_vi)
                        st.success("✅ Đã đánh dấu xử lý")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Lỗi: {ex}")
        else:
            st.info("Không có vấn đề kiểm tra nào.")

    st.divider()
    st.subheader("📈 Báo cáo kế toán")

    try:
        rpt_data = _get("/agent/v1/acct/report_snapshots", params={"limit": 20})
        rpt_items = rpt_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải báo cáo: {e}")
        rpt_items = []

    if rpt_items:
        df_rpt = pd.DataFrame(rpt_items)
        display_rpt_cols = ["report_type", "period", "version", "created_at"]
        available_rpt = [c for c in display_rpt_cols if c in df_rpt.columns]
        st.dataframe(
            df_rpt[available_rpt],
            use_container_width=True,
            column_config={
                "report_type": "Loại báo cáo",
                "period": "Kỳ",
                "version": "Phiên bản",
                "created_at": "Thời gian",
            },
        )
        with st.expander("📋 Chi tiết báo cáo mới nhất"):
            latest = rpt_items[0]
            if latest.get("summary_json"):
                st.json(latest["summary_json"])
            if latest.get("has_file"):
                st.caption("📎 Có tệp báo cáo đính kèm")
    else:
        # P0: diagnostic info when tax_export runs complete but no results
        try:
            recent_rpt = _get("/agent/v1/runs", params={"run_type": "tax_export", "limit": 1})
            ri_rpt = recent_rpt.get("items", [])
            if ri_rpt and ri_rpt[0].get("status") == "completed":
                st.info(
                    "Tác vụ **Xuất báo cáo thuế** đã chạy xong nhưng không tạo báo cáo — "
                    "có thể chưa có dữ liệu bút toán hoặc mirror Acct* trống.\n\n"
                    f"Mã tác vụ gần nhất: `{ri_rpt[0].get('run_id', '')[:12]}…`"
                )
            else:
                st.info("Chưa có báo cáo. Chạy **Xuất báo cáo thuế** ở tab Tạo tác vụ.")
        except Exception:
            st.info("Chưa có báo cáo. Chạy **Xuất báo cáo thuế** ở tab Tạo tác vụ.")


# ===== TAB 6: Dòng tiền ===============================================
with tab_cashflow:
    col_cf_hdr, col_cf_ref = st.columns([3, 1])
    with col_cf_hdr:
        st.subheader("💰 Dự báo dòng tiền")
    with col_cf_ref:
        if st.button("🔄 Làm mới", key="refresh_cashflow"):
            st.rerun()

    try:
        cf_data = _get("/agent/v1/acct/cashflow_forecast", params={"limit": 100})
        cf_items = cf_data.get("items", [])
        cf_summary = cf_data.get("summary", {})
    except Exception as e:
        st.error(f"Lỗi tải dự báo dòng tiền: {e}")
        cf_items = []
        cf_summary = {}

    if cf_summary:
        col_in, col_out, col_net = st.columns(3)
        with col_in:
            st.metric("Tổng thu dự kiến", f"{cf_summary.get('total_inflow', 0):,.0f} VND")
        with col_out:
            st.metric("Tổng chi dự kiến", f"{cf_summary.get('total_outflow', 0):,.0f} VND")
        with col_net:
            net = cf_summary.get("net", 0)
            st.metric("Ròng", f"{net:,.0f} VND", delta=f"{net:,.0f}")

    if cf_items:
        df_cf = pd.DataFrame(cf_items)
        df_cf["Hướng"] = df_cf["direction"].map(lambda d: "📈 Thu" if d == "inflow" else "📉 Chi")
        st.dataframe(
            df_cf[["forecast_date", "Hướng", "amount", "source_type", "source_ref", "confidence"]],
            use_container_width=True,
            column_config={
                "forecast_date": "Ngày dự báo",
                "amount": "Số tiền (VND)",
                "source_type": "Nguồn",
                "source_ref": "Tham chiếu",
                "confidence": "Độ tin cậy",
            },
        )
    else:
        st.info("Chưa có dự báo. Chạy **Dự báo dòng tiền** ở tab Tạo tác vụ.")


# ===== TAB 7: Chứng từ =================================================
with tab_voucher:
    col_vc_hdr, col_vc_ref = st.columns([3, 1])
    with col_vc_hdr:
        st.subheader("📥 Chứng từ đã nhập")
    with col_vc_ref:
        if st.button("🔄 Làm mới", key="refresh_voucher"):
            st.rerun()

    try:
        voucher_data = _get("/agent/v1/acct/vouchers", params={"limit": 50})
        voucher_items = voucher_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải chứng từ: {e}")
        voucher_items = []

    if voucher_items:
        df_vouchers = pd.DataFrame(voucher_items)
        display_cols = ["voucher_no", "date", "partner_name", "amount", "currency", "source", "type_hint"]
        if "classification_tag" in df_vouchers.columns:
            display_cols.append("classification_tag")
        available_cols = [c for c in display_cols if c in df_vouchers.columns]
        st.dataframe(
            df_vouchers[available_cols],
            use_container_width=True,
            column_config={
                "voucher_no": "Số chứng từ",
                "date": "Ngày",
                "partner_name": "Đối tác",
                "amount": "Số tiền",
                "currency": "Tiền tệ",
                "source": "Nguồn",
                "type_hint": "Loại gợi ý",
                "classification_tag": "Phân loại",
            },
        )
    else:
        st.info("Chưa có chứng từ nào. Chạy **Nhập chứng từ** ở tab Tạo tác vụ.")

    st.divider()
    st.subheader("🏷️ Phân loại chứng từ")

    try:
        cls_data = _get("/agent/v1/acct/voucher_classification_stats")
        cls_stats = cls_data.get("stats", [])
    except Exception as e:
        st.error(f"Lỗi tải thống kê phân loại: {e}")
        cls_stats = []

    if cls_stats:
        df_cls = pd.DataFrame(cls_stats)
        # VN labels for classification tags
        _CLS_TAG_VN: dict[str, str] = {
            "PURCHASE_INVOICE": "Hóa đơn đầu vào",
            "SALES_INVOICE": "Hóa đơn đầu ra",
            "CASH_DISBURSEMENT": "Phiếu chi",
            "CASH_RECEIPT": "Phiếu thu",
            "PAYROLL": "Lương",
            "FIXED_ASSET": "Tài sản cố định",
            "TAX_DECLARATION": "Kê khai thuế",
            "BANK_TRANSACTION": "Giao dịch ngân hàng",
            "OTHER": "Khác",
        }
        if "classification_tag" in df_cls.columns:
            df_cls["Phân loại VN"] = df_cls["classification_tag"].map(
                lambda t: _CLS_TAG_VN.get(t, t)
            )
        st.dataframe(
            df_cls,
            use_container_width=True,
            column_config={"classification_tag": "Mã phân loại", "Phân loại VN": "Phân loại", "count": "Số lượng"},
        )

        tag_options = ["(tất cả)"] + [s["classification_tag"] for s in cls_stats]
        selected_tag = st.selectbox("Lọc theo phân loại", tag_options, key="cls_filter")
        if selected_tag != "(tất cả)":
            try:
                filtered = _get(
                    "/agent/v1/acct/vouchers",
                    params={"classification_tag": selected_tag, "limit": 50},
                )
                filtered_items = filtered.get("items", [])
                if filtered_items:
                    df_f = pd.DataFrame(filtered_items)
                    st.dataframe(
                        df_f[["voucher_no", "date", "partner_name", "amount", "classification_tag"]],
                        use_container_width=True,
                        column_config={
                            "voucher_no": "Số chứng từ",
                            "date": "Ngày",
                            "partner_name": "Đối tác",
                            "amount": "Số tiền",
                            "classification_tag": "Phân loại",
                        },
                    )
                else:
                    st.info(f"Không có chứng từ với phân loại '{selected_tag}'.")
            except Exception as e:
                st.error(f"❌ Lỗi: {e}")
    else:
        st.info("Chưa có thống kê phân loại. Chạy **Phân loại chứng từ** ở tab Tạo tác vụ.")


# ===== TAB 8: Hỏi đáp =================================================
with tab_qna:
    col_qn_hdr, col_qn_ref = st.columns([3, 1])
    with col_qn_hdr:
        st.subheader("💬 Trợ lý hỏi đáp kế toán")
    with col_qn_ref:
        if st.button("🔄 Làm mới", key="refresh_qna"):
            st.rerun()

    qna_question = st.text_input(
        "Nhập câu hỏi kế toán bằng tiếng Việt", value="", key="qna_input",
        placeholder="Ví dụ: Tháng 1/2026 có bao nhiêu chứng từ?",
    )
    if st.button("📨 Gửi câu hỏi", key="qna_ask"):
        if qna_question.strip():
            with st.spinner("Đang xử lý câu hỏi…"):
                try:
                    qna_res = _post("/agent/v1/acct/qna", {"question": qna_question.strip()})
                    st.success(qna_res.get("answer", "Không có câu trả lời."))
                    with st.expander("📋 Chi tiết xử lý"):
                        meta = qna_res.get("meta", {})
                        # Display reasoning chain if available
                        chain = meta.get("reasoning_chain", [])
                        if chain:
                            st.markdown("**Chuỗi lập luận:**")
                            for i, step in enumerate(chain, 1):
                                st.markdown(f"{i}. {step}")
                            st.divider()
                        st.json({k: v for k, v in meta.items() if k != "reasoning_chain"})
                except Exception as e:
                    st.error(f"❌ Lỗi: {e}")
        else:
            st.warning("⚠️ Vui lòng nhập câu hỏi trước khi gửi.")

    with st.expander("📜 Lịch sử hỏi đáp", expanded=False):
        try:
            qna_history = _get("/agent/v1/acct/qna_audits", params={"limit": 10})
            qna_items = qna_history.get("items", [])
        except Exception as e:
            st.error(f"Lỗi: {e}")
            qna_items = []

        if qna_items:
            for item in qna_items:
                st.markdown(f"**❓ {item.get('question', '')}**")
                st.markdown(f"💡 {item.get('answer', '')}")
                st.caption(f"🕐 {item.get('created_at', '')}")
                st.divider()
        else:
            st.info("Chưa có lịch sử hỏi đáp.")


# ===== TAB 9: Hợp đồng (Labs) =========================================
with tab_contract:
    st.caption("Module hợp đồng — thử nghiệm, không phải chức năng chính.")
    st.info(
        "⚠️ **Lưu ý:** Agent chỉ tóm tắt và gom bằng chứng để hỗ trợ đọc hiểu. "
        "Quyết định kế toán vẫn thuộc về người dùng."
    )

    try:
        cases = _get("/agent/v1/contract/cases", params={"limit": 50}).get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải danh sách hợp đồng: {e}")
        cases = []

    if not cases:
        st.info("Chưa có hợp đồng nào. Chạy **Nghĩa vụ hợp đồng** ở tab Tạo tác vụ.")
    else:
        case_labels = {c["case_id"]: f"{c['case_key']} ({c['status']})" for c in cases}
        case_id = st.selectbox("Chọn hợp đồng", list(case_labels.keys()), format_func=lambda cid: case_labels[cid])

        CONFIDENCE_THRESHOLD = 0.75
        CANDIDATE_LIMIT = 5

        colC, colD = st.columns(2)
        with colC:
            st.markdown("### Nghĩa vụ — Tier B")
            try:
                obligations = _get(f"/agent/v1/contract/cases/{case_id}/obligations").get("items", [])
                if obligations:
                    high_conf = [o for o in obligations if o.get("confidence", 0) >= CONFIDENCE_THRESHOLD]
                    candidates = [o for o in obligations if o.get("confidence", 0) < CONFIDENCE_THRESHOLD]

                    _type_priority = {"payment": 0, "penalty": 1, "discount": 2}
                    candidates.sort(
                        key=lambda o: (
                            _type_priority.get(o.get("obligation_type", ""), 99),
                            -(o.get("confidence", 0)),
                        )
                    )

                    st.markdown(f"#### ✅ Độ tin cậy cao ({len(high_conf)})")
                    if high_conf:
                        df_high = pd.DataFrame(high_conf)
                        st.dataframe(
                            df_high[[
                                "obligation_type", "risk_level", "confidence",
                                "amount_value", "amount_percent", "due_date",
                            ]],
                            use_container_width=True,
                            column_config={
                                "obligation_type": "Loại nghĩa vụ",
                                "risk_level": "Mức rủi ro",
                                "confidence": "Độ tin cậy",
                                "amount_value": "Giá trị",
                                "amount_percent": "Tỷ lệ %",
                                "due_date": "Hạn trả",
                            },
                        )
                    else:
                        st.caption("Không có nghĩa vụ độ tin cậy cao.")

                    visible_candidates = candidates[:CANDIDATE_LIMIT]
                    hidden_count = max(0, len(candidates) - CANDIDATE_LIMIT)
                    st.markdown(f"#### 🔍 Ứng viên ({len(candidates)})")
                    if visible_candidates:
                        df_cand = pd.DataFrame(visible_candidates)
                        st.dataframe(
                            df_cand[[
                                "obligation_type", "risk_level", "confidence",
                                "amount_value", "amount_percent", "due_date",
                            ]],
                            use_container_width=True,
                        )
                        if hidden_count > 0:
                            with st.expander(f"Xem thêm ({hidden_count} ứng viên)"):
                                df_rest = pd.DataFrame(candidates[CANDIDATE_LIMIT:])
                                st.dataframe(
                                    df_rest[[
                                        "obligation_type", "risk_level", "confidence",
                                        "amount_value", "amount_percent", "due_date",
                                    ]],
                                    use_container_width=True,
                                )
                    else:
                        st.caption("Không có ứng viên.")

                    st.markdown("#### 📝 Đánh giá")
                    all_displayed = high_conf + visible_candidates
                    if all_displayed:
                        fb_idx = st.selectbox(
                            "Chọn nghĩa vụ để đánh giá",
                            range(len(all_displayed)),
                            format_func=lambda i: (
                                f"{all_displayed[i]['obligation_type']} "
                                f"(độ tin cậy={all_displayed[i].get('confidence', 0):.2f})"
                            ),
                            key="fb_select",
                        )
                        fb_cols = st.columns(2)
                        if _action_guard("fb_yes") or _action_guard("fb_no"):
                            st.caption("✔ Đã ghi đánh giá — đang làm mới…")
                        else:
                          with fb_cols[0]:
                            if st.button("✅ Đúng", key="fb_yes"):
                                try:
                                    _post(
                                        "/agent/v1/tier-b/feedback",
                                        {
                                            "obligation_id": all_displayed[fb_idx]["obligation_id"],
                                            "feedback_type": "explicit_yes",
                                            "user_id": current_user or None,
                                        },
                                    )
                                    st.success("✅ Đã ghi đánh giá: Đúng")
                                    _mark_done("fb_yes")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"❌ Lỗi: {ex}")
                          with fb_cols[1]:
                            if st.button("❌ Sai", key="fb_no"):
                                try:
                                    _post(
                                        "/agent/v1/tier-b/feedback",
                                        {
                                            "obligation_id": all_displayed[fb_idx]["obligation_id"],
                                            "feedback_type": "explicit_no",
                                            "user_id": current_user or None,
                                        },
                                    )
                                    st.success("❌ Đã ghi đánh giá: Sai")
                                    _mark_done("fb_no")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"❌ Lỗi: {ex}")
                else:
                    st.info("Chưa có dữ liệu nghĩa vụ. Hãy chạy phân tích hợp đồng trước.")
            except Exception as e:
                st.error(f"❌ Lỗi tải nghĩa vụ: {e}")

        with colD:
            st.markdown("### Đề xuất")
            try:
                proposals = _get(f"/agent/v1/contract/cases/{case_id}/proposals").get("items", [])
                if proposals:
                    df_prop = pd.DataFrame(proposals)
                    cols = [
                        "proposal_id", "proposal_type", "tier", "risk_level",
                        "status", "created_by", "approvals_approved", "approvals_required",
                    ]
                    st.dataframe(
                        df_prop[cols],
                        use_container_width=True,
                        column_config={
                            "proposal_id": "Mã đề xuất",
                            "proposal_type": "Loại",
                            "tier": "Cấp",
                            "risk_level": "Mức rủi ro",
                            "status": "Trạng thái",
                            "created_by": "Người tạo",
                            "approvals_approved": "Đã duyệt",
                            "approvals_required": "Cần duyệt",
                        },
                    )
                    proposal_id = st.text_input(
                        "Mã đề xuất xem chi tiết", value=df_prop.iloc[0]["proposal_id"], key="ct_pid",
                    )
                else:
                    st.info("Chưa có đề xuất.")
                    proposal_id = ""
            except Exception as e:
                st.error(f"❌ Lỗi tải đề xuất: {e}")
                proposals = []
                proposal_id = ""

            if proposal_id:
                selected = next((p for p in proposals if p["proposal_id"] == proposal_id), None)
                if selected:
                    st.markdown("#### Chi tiết đề xuất")
                    st.json(selected)

                    try:
                        approvals = (
                            _get(f"/agent/v1/contract/proposals/{proposal_id}/approvals").get("items", [])
                        )
                    except Exception:
                        approvals = []
                    if approvals:
                        st.markdown("#### Phê duyệt")
                        st.dataframe(pd.DataFrame(approvals), use_container_width=True)

                    proposal_status = selected.get("status", "")
                    is_finalized = proposal_status in {"approved", "rejected"}

                    if is_finalized:
                        _label = "✅ Đã duyệt" if proposal_status == "approved" else "❌ Đã từ chối"
                        st.info(f"{_label} — trạng thái: **{proposal_status}**")

                    evidence_ack = st.checkbox(
                        "Tôi đã xem xét bằng chứng", value=False, disabled=is_finalized, key="ct_ack",
                    )
                    note = st.text_input("Ghi chú (tùy chọn)", value="", disabled=is_finalized, key="ct_note")

                    maker = (selected.get("created_by") or "").strip()
                    if maker and maker == current_user:
                        st.warning("⚠️ Maker-checker: bạn không thể duyệt đề xuất do chính mình tạo.")
                        can_act = False
                    else:
                        can_act = not is_finalized

                    _gk_ct_a = f"ct_approve_{proposal_id}"
                    _gk_ct_r = f"ct_reject_{proposal_id}"
                    if _action_guard(_gk_ct_a) or _action_guard(_gk_ct_r):
                        st.caption("✔ Đã xử lý — đang làm mới…")
                    else:
                        colX, colY = st.columns(2)
                        with colX:
                            if st.button("✅ Duyệt", disabled=(not can_act) or (not evidence_ack), key=_gk_ct_a):
                                try:
                                    _post(
                                        f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                        {
                                            "decision": "approve",
                                            "approver_id": current_user,
                                            "evidence_ack": evidence_ack,
                                            "note": note or None,
                                        },
                                    )
                                    _mark_done(_gk_ct_a)
                                    st.success("✅ Đã gửi phê duyệt")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ {e}")
                        with colY:
                            if st.button("❌ Từ chối", disabled=(not can_act) or (not evidence_ack), key=_gk_ct_r):
                                try:
                                    _post(
                                        f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                        {
                                            "decision": "reject",
                                            "approver_id": current_user,
                                            "evidence_ack": evidence_ack,
                                            "note": note or None,
                                        },
                                    )
                                    _mark_done(_gk_ct_r)
                                    st.success("❌ Đã từ chối đề xuất")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ {e}")
