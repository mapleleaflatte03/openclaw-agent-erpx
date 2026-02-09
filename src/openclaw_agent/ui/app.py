from __future__ import annotations

import contextlib
import os
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
    "working_papers": "Working papers",
    "soft_checks": "Kiểm tra logic",
    "ar_dunning": "Nhắc nợ (AR Dunning)",
    "close_checklist": "Checklist kết kỳ",
    "evidence_pack": "Gói bằng chứng",
    "kb_index": "Cập nhật kho tri thức",
    "contract_obligation": "Nghĩa vụ hợp đồng",
}

_RUN_TYPE_ORDER = list(_RUN_TYPE_LABELS.keys())

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
        raise RuntimeError("Không thể kết nối API backend.") from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError("API backend phản hồi quá chậm (timeout).") from e


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
        raise RuntimeError("Không thể kết nối API backend.") from e
    except requests.exceptions.Timeout as e:
        raise RuntimeError("API backend phản hồi quá chậm (timeout).") from e


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=os.getenv("MINIO_REGION", "sgp1"),
    )


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="ERP-X AI Kế toán – OpenClaw", layout="wide")

# CSS fix: ensure DataFrame toolbar (Download CSV) is clickable above glide overlay
st.markdown(
    """
    <style>
    [data-testid="stDataFrame"] [data-testid="stElementToolbar"] {
        z-index: 100 !important;
        pointer-events: auto !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧾 ERP-X AI Kế toán")
st.caption("OpenClaw — Hỗ trợ đọc, phân loại & đối chiếu chứng từ (READ-ONLY)")
# Internal endpoint shown only when DEBUG_UI=true
if DEBUG_UI:
    with st.expander("⚙️ Dev / Debug info", expanded=False):
        st.caption(f"Agent API: {AGENT_BASE_URL}")

current_user = _DEMO_USER_ID

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
(
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
    "📋 Tạo tác vụ",
    "📂 Quản lý tác vụ",
    "🧾 Bút toán đề xuất",
    "🔍 Giao dịch bất thường",
    "📊 Kiểm tra & Báo cáo",
    "💰 Dòng tiền",
    "📥 Chứng từ",
    "💬 Hỏi đáp",
    "🔬 Hợp đồng (Labs)",
])


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
            payload["exception_id"] = st.text_input("exception_id", value="")
            payload["issue_id"] = st.text_input("issue_id (tùy chọn)", value="")
        if run_type == "kb_index":
            payload["file_uri"] = st.text_input("Đường dẫn file", value="")
            payload["title"] = st.text_input("Tiêu đề (tùy chọn)", value="")
            payload["doc_type"] = st.selectbox("Loại tài liệu", ["process", "law", "template"])
            payload["version"] = st.text_input("Phiên bản", value="v1")
        if run_type == "contract_obligation":
            payload["case_key"] = st.text_input("case_key (tùy chọn)", value="")
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

        idem = st.text_input("Idempotency-Key (tùy chọn)", value="", key="trig_idem")

        if st.button("▶️ Chạy tác vụ", key="trig_run"):
            # --- form validation ---
            if _period_required and not (payload.get("period") or "").strip():
                st.error("❌ Vui lòng nhập kỳ kế toán (period) — trường bắt buộc.")
            else:
                body: dict[str, Any] = {"run_type": run_type, "trigger_type": "manual", "payload": payload}
                if requested_by.strip():
                    body["requested_by"] = requested_by.strip()
                try:
                    res = _post("/agent/v1/runs", body, idem or None)
                    st.success(
                        f"✅ Tác vụ đã được tạo: {res.get('run_id', '')} "
                        f"(trạng thái: {res.get('status', '')})"
                    )
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {e}")

    with col2:
        st.subheader("Tải file lên (Event Trigger)")
        mode = st.selectbox("Loại file", ["attachments", "kb"], key="drop_mode")
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

    try:
        runs = _get("/agent/v1/runs", params={"limit": 50}).get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải danh sách tác vụ: {e}")
        runs = []
    if runs:
        df = pd.DataFrame(runs)
        df["run_type_label"] = df["run_type"].map(lambda rt: _RUN_TYPE_LABELS.get(rt, rt))
        st.dataframe(
            df[["run_id", "run_type_label", "status", "trigger_type", "created_at"]],
            use_container_width=True,
            column_config={"run_type_label": "Loại tác vụ"},
        )
        run_id = st.text_input("Run ID xem chi tiết", value=df.iloc[0]["run_id"], key="runs_inspect")

        if run_id:
            colA, colB = st.columns(2)
            with colA:
                st.markdown("### Bước xử lý (Tasks)")
                try:
                    tasks = _get("/agent/v1/tasks", params={"run_id": run_id}).get("items", [])
                except Exception as e:
                    st.error(f"Lỗi tải tasks: {e}")
                    tasks = []
                if tasks:
                    st.dataframe(
                        pd.DataFrame(tasks)[["task_name", "status", "error", "created_at"]],
                        use_container_width=True,
                    )
            with colB:
                st.markdown("### Nhật ký (Logs)")
                try:
                    logs = _get("/agent/v1/logs", params={"run_id": run_id, "limit": 200}).get("items", [])
                except Exception as e:
                    st.error(f"Lỗi tải logs: {e}")
                    logs = []
                if logs:
                    st.dataframe(
                        pd.DataFrame(logs)[["ts", "level", "message"]],
                        use_container_width=True,
                    )
    else:
        st.info("Chưa có tác vụ. Tạo mới ở tab **Tạo tác vụ**.")


# ===== TAB 3: Bút toán đề xuất ========================================
with tab_journal:
    col_jp_hdr, col_jp_ref = st.columns([3, 1])
    with col_jp_hdr:
        st.subheader("🧾 Bút toán đề xuất (Journal Proposals)")
    with col_jp_ref:
        if st.button("🔄 Làm mới", key="refresh_journal"):
            st.rerun()

    st.markdown(f"👤 Người duyệt (demo): **{current_user}**")

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
                    f"**{status_icon} {p.get('description', '')}** — "
                    f"Độ tin cậy: {p.get('confidence', 0):.0%}  \n"
                    f"📝 {lines_str}"
                )
            with col_p2:
                if p.get("status") == "pending":
                    _btn_key_a = f"approve_{p['id']}"
                    _btn_key_r = f"reject_{p['id']}"
                    # Double-click guard via session_state
                    if st.session_state.get(f"done_{_btn_key_a}") or st.session_state.get(f"done_{_btn_key_r}"):
                        st.caption("✔ Đã xử lý — đang làm mới…")
                    else:
                        col_a, col_r = st.columns(2)
                        with col_a:
                            if st.button("✅ Duyệt", key=_btn_key_a):
                                try:
                                    _post(
                                        f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                        {"status": "approved", "reviewed_by": current_user},
                                    )
                                    st.session_state[f"done_{_btn_key_a}"] = True
                                    st.success("Đã duyệt")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(str(ex))
                        with col_r:
                            if st.button("❌ Từ chối", key=_btn_key_r):
                                try:
                                    _post(
                                        f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                        {"status": "rejected", "reviewed_by": current_user},
                                    )
                                    st.session_state[f"done_{_btn_key_r}"] = True
                                    st.success("Đã từ chối")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(str(ex))
                else:
                    st.caption(f"{p.get('status', '')} bởi {p.get('reviewed_by', '')}")
    else:
        st.info("Chưa có bút toán đề xuất. Chạy **Đề xuất bút toán** ở tab Tạo tác vụ.")


# ===== TAB 4: Giao dịch bất thường ====================================
with tab_anomaly:
    col_an_hdr, col_an_ref = st.columns([3, 1])
    with col_an_hdr:
        st.subheader("🔍 Giao dịch bất thường (Anomaly Flags)")
    with col_an_ref:
        if st.button("🔄 Làm mới", key="refresh_anomaly"):
            st.rerun()

    try:
        anomalies_data = _get("/agent/v1/acct/anomaly_flags", params={"limit": 50})
        anomalies = anomalies_data.get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải anomaly flags: {e}")
        anomalies = []

    if anomalies:
        df_anom = pd.DataFrame(anomalies)
        severity_colors = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        df_anom["mức_độ"] = df_anom["severity"].map(lambda s: severity_colors.get(s, "⚪") + " " + s)
        st.dataframe(
            df_anom[["mức_độ", "anomaly_type", "description", "resolution", "created_at"]],
            use_container_width=True,
            column_config={"mức_độ": "Mức độ"},
        )

        open_flags = [a for a in anomalies if a.get("resolution") == "open"]
        if open_flags:
            flag_id = st.selectbox(
                "Chọn flag để xử lý",
                [f["id"] for f in open_flags],
                format_func=lambda fid: next(
                    (f"{f['anomaly_type']}: {f['description'][:50]}..." for f in open_flags if f["id"] == fid),
                    fid,
                ),
                key="an_select",
            )
            col_res, col_ign = st.columns(2)
            with col_res:
                if st.button("✅ Đã xử lý", key="an_resolve"):
                    try:
                        _post(
                            f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                            {"resolution": "resolved", "resolved_by": current_user},
                        )
                        st.success("Đã giải quyết")
                        st.rerun()
                    except Exception as ex:
                        st.error(str(ex))
            with col_ign:
                if st.button("⏭️ Bỏ qua", key="an_ignore"):
                    try:
                        _post(
                            f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                            {"resolution": "ignored", "resolved_by": current_user},
                        )
                        st.success("Đã bỏ qua")
                        st.rerun()
                    except Exception as ex:
                        st.error(str(ex))
    else:
        st.info("Chưa có anomaly flags. Chạy **Đối chiếu ngân hàng** ở tab Tạo tác vụ.")


# ===== TAB 5: Kiểm tra & Báo cáo ======================================
with tab_check:
    col_ck_hdr, col_ck_ref = st.columns([3, 1])
    with col_ck_hdr:
        st.subheader("📊 Kiểm tra logic (Soft Check Results)")
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
        st.dataframe(
            df_scr[["period", "total_checks", "passed", "warnings", "errors", "score", "created_at"]],
            use_container_width=True,
        )
    else:
        st.info("Chưa có kết quả kiểm tra. Chạy **Kiểm tra logic** ở tab Tạo tác vụ.")

    # --- Validation Issues ---
    with st.expander("🔎 Chi tiết — Vấn đề kiểm tra (Validation Issues)", expanded=bool(scr_items)):
        issue_filter = st.selectbox(
            "Lọc trạng thái", ["open", "resolved", "ignored", "(tất cả)"], key="vi_filter",
        )
        try:
            vi_params: dict[str, Any] = {"limit": 50}
            if issue_filter != "(tất cả)":
                vi_params["resolution"] = issue_filter
            vi_data = _get("/agent/v1/acct/validation_issues", params=vi_params)
            vi_items = vi_data.get("items", [])
        except Exception as e:
            st.error(f"Lỗi tải validation issues: {e}")
            vi_items = []

        if vi_items:
            df_vi = pd.DataFrame(vi_items)
            st.dataframe(
                df_vi[["rule_code", "severity", "message", "erp_ref", "resolution", "created_at"]],
                use_container_width=True,
            )

            resolve_id = st.text_input("Issue ID để xử lý", value="", key="resolve_vi_id")
            if resolve_id and st.button("✅ Đánh dấu đã xử lý", key="resolve_vi_btn"):
                try:
                    _post(
                        f"/agent/v1/acct/validation_issues/{resolve_id}/resolve",
                        {"action": "resolved", "resolved_by": current_user},
                    )
                    st.success("Đã đánh dấu xử lý")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Lỗi: {ex}")
        else:
            st.info("Không có vấn đề kiểm tra.")

    st.divider()
    st.subheader("📈 Báo cáo kế toán (Report Snapshots)")

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
        st.dataframe(df_rpt[available_rpt], use_container_width=True)
        with st.expander("📋 Chi tiết báo cáo mới nhất"):
            latest = rpt_items[0]
            if latest.get("summary_json"):
                st.json(latest["summary_json"])
            if latest.get("has_file"):
                st.caption("📎 Có tệp báo cáo đính kèm")
    else:
        st.info("Chưa có báo cáo. Chạy **Xuất báo cáo thuế** ở tab Tạo tác vụ.")


# ===== TAB 6: Dòng tiền ===============================================
with tab_cashflow:
    col_cf_hdr, col_cf_ref = st.columns([3, 1])
    with col_cf_hdr:
        st.subheader("💰 Dự báo dòng tiền (Cashflow Forecast)")
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
        st.dataframe(
            df_cf[["forecast_date", "direction", "amount", "source_type", "source_ref", "confidence"]],
            use_container_width=True,
        )
    else:
        st.info("Chưa có dự báo. Chạy **Dự báo dòng tiền** ở tab Tạo tác vụ.")


# ===== TAB 7: Chứng từ =================================================
with tab_voucher:
    col_vc_hdr, col_vc_ref = st.columns([3, 1])
    with col_vc_hdr:
        st.subheader("📥 Chứng từ đã ingest")
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
        st.dataframe(df_vouchers[available_cols], use_container_width=True)
    else:
        st.info("Chưa có chứng từ. Chạy **Nhập chứng từ** ở tab Tạo tác vụ.")

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
        st.dataframe(df_cls, use_container_width=True)

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
                    )
                else:
                    st.info(f"Không có chứng từ với phân loại '{selected_tag}'.")
            except Exception as e:
                st.error(f"Lỗi: {e}")
    else:
        st.info("Chưa có thống kê phân loại. Chạy **Phân loại chứng từ** ở tab Tạo tác vụ.")


# ===== TAB 8: Hỏi đáp =================================================
with tab_qna:
    col_qn_hdr, col_qn_ref = st.columns([3, 1])
    with col_qn_hdr:
        st.subheader("💬 Trợ lý Q&A kế toán")
    with col_qn_ref:
        if st.button("🔄 Làm mới", key="refresh_qna"):
            st.rerun()

    qna_question = st.text_input("Nhập câu hỏi kế toán", value="", key="qna_input")
    if st.button("Hỏi", key="qna_ask"):
        if qna_question.strip():
            try:
                qna_res = _post("/agent/v1/acct/qna", {"question": qna_question.strip()})
                st.success(qna_res.get("answer", ""))
                with st.expander("Chi tiết"):
                    st.json(qna_res.get("meta", {}))
            except Exception as e:
                st.error(f"Lỗi: {e}")
        else:
            st.warning("Vui lòng nhập câu hỏi.")

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
    st.caption("Module hợp đồng — thử nghiệm, không phải core product.")
    st.info(
        "⚠️ **Lưu ý:** Agent chỉ tóm tắt và gom bằng chứng để hỗ trợ đọc hiểu. "
        "Quyết định kế toán vẫn thuộc về người dùng."
    )

    try:
        cases = _get("/agent/v1/contract/cases", params={"limit": 50}).get("items", [])
    except Exception as e:
        st.error(f"Lỗi tải hợp đồng: {e}")
        cases = []

    if not cases:
        st.info("Chưa có hợp đồng. Chạy **Nghĩa vụ hợp đồng** ở tab Tạo tác vụ.")
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
                            with st.expander(f"Xem thêm ({hidden_count})"):
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
                                f"(conf={all_displayed[i].get('confidence', 0):.2f})"
                            ),
                            key="fb_select",
                        )
                        fb_cols = st.columns(2)
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
                                    st.success("Đã ghi đánh giá: Đúng")
                                except Exception as ex:
                                    st.error(f"Lỗi: {ex}")
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
                                    st.success("Đã ghi đánh giá: Sai")
                                except Exception as ex:
                                    st.error(f"Lỗi: {ex}")
                else:
                    st.info("Chưa có nghĩa vụ.")
            except Exception as e:
                st.error(f"Lỗi tải nghĩa vụ: {e}")

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
                    st.dataframe(df_prop[cols], use_container_width=True)
                    proposal_id = st.text_input(
                        "Proposal ID xem chi tiết", value=df_prop.iloc[0]["proposal_id"], key="ct_pid",
                    )
                else:
                    st.info("Chưa có đề xuất.")
                    proposal_id = ""
            except Exception as e:
                st.error(f"Lỗi tải đề xuất: {e}")
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
                        st.warning("Maker-checker: bạn không thể duyệt đề xuất của chính mình.")
                        can_act = False
                    else:
                        can_act = not is_finalized

                    colX, colY = st.columns(2)
                    with colX:
                        if st.button("✅ Duyệt", disabled=(not can_act) or (not evidence_ack), key="ct_approve"):
                            try:
                                res = _post(
                                    f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                    {
                                        "decision": "approve",
                                        "approver_id": current_user,
                                        "evidence_ack": evidence_ack,
                                        "note": note or None,
                                    },
                                )
                                st.success(res)
                                st.rerun()
                            except Exception as e:
                                st.error(e)
                    with colY:
                        if st.button(
                            "❌ Từ chối", disabled=(not can_act) or (not evidence_ack), key="ct_reject",
                        ):
                            try:
                                res = _post(
                                    f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                    {
                                        "decision": "reject",
                                        "approver_id": current_user,
                                        "evidence_ack": evidence_ack,
                                        "note": note or None,
                                    },
                                )
                                st.success(res)
                                st.rerun()
                            except Exception as e:
                                st.error(e)
