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


def _headers() -> dict[str, str]:
    h = {}
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

col1, col2 = st.columns(2)
with col1:
    st.subheader("Trigger Manual Run")
    requested_by = st.text_input("requested_by (optional user id)", value="")
    run_type = st.selectbox(
        "run_type",
        [
            "journal_suggestion",
            "bank_reconcile",
            "cashflow_forecast",
            "tax_export",
            "working_papers",
            "soft_checks",
            "ar_dunning",
            "close_checklist",
            "evidence_pack",
            "kb_index",
            "contract_obligation",
        ],
    )
    payload: dict[str, Any] = {}
    if run_type in {"tax_export", "working_papers", "close_checklist"}:
        payload["period"] = st.text_input("period (YYYY-MM) *", value=date.today().strftime("%Y-%m"))
    if run_type == "soft_checks":
        payload["updated_after"] = st.text_input("updated_after (ISO)", value="")
        payload["period"] = st.text_input("period (optional YYYY-MM)", value=date.today().strftime("%Y-%m"))
    if run_type == "cashflow_forecast":
        payload["period"] = st.text_input("period (YYYY-MM)", value=date.today().strftime("%Y-%m"))
        payload["horizon_days"] = st.number_input("horizon_days", min_value=7, max_value=90, value=30)
    if run_type == "ar_dunning":
        payload["as_of"] = st.text_input("as_of (YYYY-MM-DD)", value=date.today().isoformat())
    if run_type == "evidence_pack":
        payload["exception_id"] = st.text_input("exception_id", value="")
        payload["issue_id"] = st.text_input("issue_id (optional)", value="")
    if run_type == "kb_index":
        payload["file_uri"] = st.text_input("file_uri (s3://... or local path)", value="")
        payload["title"] = st.text_input("title (optional)", value="")
        payload["doc_type"] = st.selectbox("doc_type", ["process", "law", "template"])
        payload["version"] = st.text_input("version", value="v1")
    if run_type == "contract_obligation":
        payload["case_key"] = st.text_input("case_key (optional)", value="")
        payload["partner_name"] = st.text_input("partner_name (optional)", value="")
        payload["partner_tax_id"] = st.text_input("partner_tax_id (optional MST)", value="")
        payload["contract_code"] = st.text_input("contract_code (optional)", value="")
        payload["contract_files"] = [
            x.strip()
            for x in st.text_area("contract_files (one per line: s3://... or local path)").splitlines()
            if x.strip()
        ]
        payload["email_files"] = [
            x.strip()
            for x in st.text_area("email_files (one per line: .eml or .txt path)").splitlines()
            if x.strip()
        ]

    idem = st.text_input("Idempotency-Key (optional)", value="")
    if st.button("Run"):
        body = {"run_type": run_type, "trigger_type": "manual", "payload": payload}
        if requested_by.strip():
            body["requested_by"] = requested_by.strip()
        try:
            res = _post("/agent/v1/runs", body, idem or None)
            st.success(f"✅ Run đã được tạo: {res.get('run_id', '')} (status: {res.get('status', '')})")
        except Exception as e:
            st.error(f"❌ {e}")

with col2:
    st.subheader("Upload Drop File (Event Trigger)")
    mode = st.selectbox("drop_type", ["attachments", "kb"])
    up = st.file_uploader("file", type=None)
    if up is not None and st.button("Upload to MinIO Drop"):
        key = f"drop/{mode}/{int(time.time())}_{up.name}"
        s3 = _s3()
        s3.put_object(Bucket=MINIO_BUCKET_DROP, Key=key, Body=up.getvalue())
        st.success({"bucket": MINIO_BUCKET_DROP, "key": key, "file_uri": f"s3://{MINIO_BUCKET_DROP}/{key}"})

st.divider()
col_runs_hdr, col_refresh = st.columns([3, 1])
with col_runs_hdr:
    st.subheader("Runs")
with col_refresh:
    if st.button("🔄 Refresh", key="refresh_all"):
        st.rerun()

try:
    runs = _get("/agent/v1/runs", params={"limit": 50}).get("items", [])
except Exception as e:
    st.error(f"Lỗi tải runs: {e}")
    runs = []
if runs:
    df = pd.DataFrame(runs)
    st.dataframe(df[["run_id", "run_type", "status", "trigger_type", "created_at"]], use_container_width=True)
    run_id = st.text_input("run_id to inspect", value=df.iloc[0]["run_id"])

    if run_id:
        colA, colB = st.columns(2)
        with colA:
            st.markdown("### Tasks")
            try:
                tasks = _get("/agent/v1/tasks", params={"run_id": run_id}).get("items", [])
            except Exception as e:
                st.error(f"Lỗi tải tasks: {e}")
                tasks = []
            if tasks:
                st.dataframe(pd.DataFrame(tasks)[["task_name", "status", "error", "created_at"]], use_container_width=True)
        with colB:
            st.markdown("### Logs")
            try:
                logs = _get("/agent/v1/logs", params={"run_id": run_id, "limit": 200}).get("items", [])
            except Exception as e:
                st.error(f"Lỗi tải logs: {e}")
                logs = []
            if logs:
                st.dataframe(pd.DataFrame(logs)[["ts", "level", "message"]], use_container_width=True)
else:
    st.info("No runs yet. Trigger one above.")

st.divider()
st.subheader("🧾 Bút toán đề xuất (Journal Proposals)")

# P0 security: current_user_id from env, not editable by user
_DEMO_USER_ID = os.getenv("OPENCLAW_DEMO_USER_ID", "demo-checker")
current_user = _DEMO_USER_ID
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
            f"{'Nợ' if ln.get('debit',0)>0 else 'Có'} TK {ln.get('account_code','')} "
            f"({ln.get('account_name','')}) {ln.get('debit',0) or ln.get('credit',0):,.0f}"
            for ln in p.get("lines", [])
        )
        status_icon = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(p.get("status",""), "❓")
        col_p1, col_p2 = st.columns([3, 1])
        with col_p1:
            st.markdown(
                f"**{status_icon} {p.get('description', '')}** — "
                f"Confidence: {p.get('confidence', 0):.0%}  \n"
                f"📝 {lines_str}"
            )
        with col_p2:
            if p.get("status") == "pending":
                col_a, col_r = st.columns(2)
                with col_a:
                    if st.button("✅ Duyệt", key=f"approve_{p['id']}"):
                        try:
                            _post(
                                f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                {"status": "approved", "reviewed_by": current_user},
                            )
                            st.success("Đã duyệt")
                            st.rerun()
                        except Exception as ex:
                            st.error(str(ex))
                with col_r:
                    if st.button("❌ Từ chối", key=f"reject_{p['id']}"):
                        try:
                            _post(
                                f"/agent/v1/acct/journal_proposals/{p['id']}/review",
                                {"status": "rejected", "reviewed_by": current_user},
                            )
                            st.success("Đã từ chối")
                            st.rerun()
                        except Exception as ex:
                            st.error(str(ex))
            else:
                st.caption(f"{p.get('status','')} by {p.get('reviewed_by','')}")
else:
    st.info("Chưa có bút toán đề xuất. Chạy `journal_suggestion` ở trên.")


st.divider()
st.subheader("🔍 Giao dịch bất thường (Anomaly Flags)")

try:
    anomalies_data = _get("/agent/v1/acct/anomaly_flags", params={"limit": 50})
    anomalies = anomalies_data.get("items", [])
except Exception as e:
    st.error(f"Lỗi tải anomaly flags: {e}")
    anomalies = []

if anomalies:
    df_anom = pd.DataFrame(anomalies)
    severity_colors = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    df_anom["sev"] = df_anom["severity"].map(lambda s: severity_colors.get(s, "⚪") + " " + s)
    st.dataframe(
        df_anom[["sev", "anomaly_type", "description", "resolution", "created_at"]],
        use_container_width=True,
        column_config={"sev": "Severity"},
    )

    open_flags = [a for a in anomalies if a.get("resolution") == "open"]
    if open_flags:
        flag_id = st.selectbox(
            "Flag ID để xử lý",
            [f["id"] for f in open_flags],
            format_func=lambda fid: next(
                (f"{f['anomaly_type']}: {f['description'][:50]}..." for f in open_flags if f["id"] == fid),
                fid,
            ),
        )
        col_res, col_ign = st.columns(2)
        with col_res:
            if st.button("✅ Resolved"):
                try:
                    _post(f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                          {"resolution": "resolved", "resolved_by": current_user})
                    st.success("Đã giải quyết")
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
        with col_ign:
            if st.button("⏭️ Ignore"):
                try:
                    _post(f"/agent/v1/acct/anomaly_flags/{flag_id}/resolve",
                          {"resolution": "ignored", "resolved_by": current_user})
                    st.success("Đã bỏ qua")
                    st.rerun()
                except Exception as ex:
                    st.error(str(ex))
else:
    st.info("Chưa có anomaly flags. Chạy `bank_reconcile` ở trên.")


st.divider()
# Contract review → Labs/Experimental
with st.expander("🔬 Experimental: Contract Obligation Review (Labs)", expanded=False):
    st.caption("Module hợp đồng — experimental, không phải core product.")

    try:
        cases = _get("/agent/v1/contract/cases", params={"limit": 50}).get("items", [])
    except Exception as e:
        st.error(f"Failed to load contract cases: {e}")
        cases = []

    if not cases:
        st.info("No contract cases yet. Trigger a `contract_obligation` run above.")
    else:
        case_labels = {c["case_id"]: f"{c['case_key']} ({c['status']})" for c in cases}
        case_id = st.selectbox("case_id", list(case_labels.keys()), format_func=lambda cid: case_labels[cid])

        # --- Tier B disclaimer (Design Principles §1, §2) ---
        st.info(
            "⚠️ **Disclaimer:** Agent chỉ tóm tắt và gom bằng chứng để hỗ trợ đọc hiểu. "
            "Quyết định kế toán vẫn thuộc về người dùng."
        )

        CONFIDENCE_THRESHOLD = 0.75
        CANDIDATE_LIMIT = 5

        colC, colD = st.columns(2)
        with colC:
            st.markdown("### Obligations — Tier B")
            try:
                obligations = _get(f"/agent/v1/contract/cases/{case_id}/obligations").get("items", [])
                if obligations:
                    high_conf = [o for o in obligations if o.get("confidence", 0) >= CONFIDENCE_THRESHOLD]
                    candidates = [o for o in obligations if o.get("confidence", 0) < CONFIDENCE_THRESHOLD]

                    # Sort candidates: payment > penalty > discount > other
                    _type_priority = {"payment": 0, "penalty": 1, "discount": 2}
                    candidates.sort(
                        key=lambda o: (
                            _type_priority.get(o.get("obligation_type", ""), 99),
                            -(o.get("confidence", 0)),
                        )
                    )

                    # --- High-confidence ---
                    st.markdown(f"#### ✅ High-confidence ({len(high_conf)})")
                    if high_conf:
                        df_high = pd.DataFrame(high_conf)
                        st.dataframe(
                            df_high[
                                [
                                    "obligation_type",
                                    "risk_level",
                                    "confidence",
                                    "amount_value",
                                    "amount_percent",
                                    "due_date",
                                ]
                            ],
                            use_container_width=True,
                        )
                    else:
                        st.caption("Không có nghĩa vụ high-confidence.")

                    # --- Candidates ---
                    visible_candidates = candidates[:CANDIDATE_LIMIT]
                    hidden_count = max(0, len(candidates) - CANDIDATE_LIMIT)
                    st.markdown(f"#### 🔍 Candidates ({len(candidates)})")
                    if visible_candidates:
                        df_cand = pd.DataFrame(visible_candidates)
                        st.dataframe(
                            df_cand[
                                [
                                    "obligation_type",
                                    "risk_level",
                                    "confidence",
                                    "amount_value",
                                    "amount_percent",
                                    "due_date",
                                ]
                            ],
                            use_container_width=True,
                        )
                        if hidden_count > 0:
                            with st.expander(f"Xem thêm ({hidden_count})"):
                                df_rest = pd.DataFrame(candidates[CANDIDATE_LIMIT:])
                                st.dataframe(
                                    df_rest[
                                        [
                                            "obligation_type",
                                            "risk_level",
                                            "confidence",
                                            "amount_value",
                                            "amount_percent",
                                            "due_date",
                                        ]
                                    ],
                                    use_container_width=True,
                                )
                    else:
                        st.caption("Không có candidates.")

                    # --- Micro-feedback (explicit Đúng/Sai) ---
                    st.markdown("#### 📝 Feedback")
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
                                    st.success("Đã ghi feedback: Đúng")
                                except Exception as ex:
                                    st.error(f"Lỗi ghi feedback: {ex}")
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
                                    st.success("Đã ghi feedback: Sai")
                                except Exception as ex:
                                    st.error(f"Lỗi ghi feedback: {ex}")
                else:
                    st.info("No obligations yet.")
            except Exception as e:
                st.error(f"Failed to load obligations: {e}")

        with colD:
            st.markdown("### Proposals")
            try:
                proposals = _get(f"/agent/v1/contract/cases/{case_id}/proposals").get("items", [])
                if proposals:
                    df = pd.DataFrame(proposals)
                    cols = [
                        "proposal_id",
                        "proposal_type",
                        "tier",
                        "risk_level",
                        "status",
                        "created_by",
                        "approvals_approved",
                        "approvals_required",
                    ]
                    st.dataframe(df[cols], use_container_width=True)
                    proposal_id = st.text_input("proposal_id to act on", value=df.iloc[0]["proposal_id"])
                else:
                    st.info("No proposals yet.")
                    proposal_id = ""
            except Exception as e:
                st.error(f"Failed to load proposals: {e}")
                proposals = []
                proposal_id = ""

            if proposal_id:
                selected = next((p for p in proposals if p["proposal_id"] == proposal_id), None)
                if selected:
                    st.markdown("#### Proposal Details")
                    st.json(selected)

                    try:
                        approvals = _get(f"/agent/v1/contract/proposals/{proposal_id}/approvals").get("items", [])
                    except Exception:
                        approvals = []
                    if approvals:
                        st.markdown("#### Approvals")
                        st.dataframe(pd.DataFrame(approvals), use_container_width=True)

                    # Check if proposal is already finalized
                    proposal_status = selected.get("status", "")
                    is_finalized = proposal_status in {"approved", "rejected"}

                    if is_finalized:
                        _label = "✅ Đã duyệt" if proposal_status == "approved" else "❌ Đã từ chối"
                        st.info(f"{_label} — trạng thái: **{proposal_status}**")

                evidence_ack = st.checkbox("I have reviewed evidence", value=False, disabled=is_finalized)
                note = st.text_input("note (optional)", value="", disabled=is_finalized)

                maker = (selected.get("created_by") or "").strip()
                if maker and maker == current_user:
                    st.warning("Maker-checker: you cannot approve your own proposal.")
                    can_act = False
                else:
                    can_act = not is_finalized

                colX, colY = st.columns(2)
                with colX:
                    if st.button("Approve", disabled=(not can_act) or (not evidence_ack)):
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
                    if st.button("Reject", disabled=(not can_act) or (not evidence_ack)):
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

# ---------------------------------------------------------------------------
# Phase 2: Extended Accounting Dashboard
# ---------------------------------------------------------------------------

st.divider()
st.subheader("📊 Kiểm tra logic (Soft Check Results)")

try:
    scr_data = _get("/agent/v1/acct/soft_check_results", params={"limit": 10})
    scr_items = scr_data.get("items", [])
except Exception as e:
    st.error(f"Lỗi tải soft check results: {e}")
    scr_items = []

if scr_items:
    df_scr = pd.DataFrame(scr_items)
    st.dataframe(
        df_scr[["period", "total_checks", "passed", "warnings", "errors", "score", "created_at"]],
        use_container_width=True,
    )
else:
    st.info("Chưa có kết quả kiểm tra. Hãy chạy 'soft_checks' ở trên.")

# --- Validation Issues ---
with st.expander("🔎 Chi tiết — Validation Issues", expanded=False):
    issue_filter = st.selectbox("Lọc trạng thái", ["open", "resolved", "ignored", "(tất cả)"], key="vi_filter")
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

        # Resolve action
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
        st.info("Không có validation issues.")


st.divider()
st.subheader("📈 Báo cáo kế toán (Report Snapshots)")

try:
    rpt_data = _get("/agent/v1/acct/report_snapshots", params={"limit": 20})
    rpt_items = rpt_data.get("items", [])
except Exception as e:
    st.error(f"Lỗi tải reports: {e}")
    rpt_items = []

if rpt_items:
    df_rpt = pd.DataFrame(rpt_items)
    st.dataframe(
        df_rpt[["report_type", "period", "version", "created_at"]],
        use_container_width=True,
    )
    # Show summary of first report
    with st.expander("📋 Chi tiết báo cáo mới nhất"):
        latest = rpt_items[0]
        if latest.get("summary_json"):
            st.json(latest["summary_json"])
        if latest.get("file_uri"):
            st.caption(f"File: {latest['file_uri']}")
else:
    st.info("Chưa có báo cáo. Hãy chạy 'tax_export' ở trên.")


st.divider()
st.subheader("💰 Dự báo dòng tiền (Cashflow Forecast)")

try:
    cf_data = _get("/agent/v1/acct/cashflow_forecast", params={"limit": 100})
    cf_items = cf_data.get("items", [])
    cf_summary = cf_data.get("summary", {})
except Exception as e:
    st.error(f"Lỗi tải cashflow forecast: {e}")
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
    st.info("Chưa có dự báo. Hãy chạy 'cashflow_forecast' ở trên.")
