from __future__ import annotations

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
    r = requests.get(f"{AGENT_BASE_URL}{path}", params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _post(path: str, json_body: dict[str, Any], idem: str | None = None) -> Any:
    headers = {"Content-Type": "application/json", **_headers()}
    if idem:
        headers["Idempotency-Key"] = idem
    r = requests.post(f"{AGENT_BASE_URL}{path}", json=json_body, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=os.getenv("MINIO_REGION", "sgp1"),
    )


st.set_page_config(page_title="OpenClaw Agent Ops", layout="wide")
st.title("OpenClaw Agent Ops UI")
st.caption(f"Agent API: {AGENT_BASE_URL}")

col1, col2 = st.columns(2)
with col1:
    st.subheader("Trigger Manual Run")
    requested_by = st.text_input("requested_by (optional user id)", value="")
    run_type = st.selectbox(
        "run_type",
        [
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
        payload["period"] = st.text_input("period (YYYY-MM)", value=date.today().strftime("%Y-%m"))
    if run_type == "soft_checks":
        payload["updated_after"] = st.text_input("updated_after (ISO)", value="")
        payload["period"] = st.text_input("period (optional YYYY-MM)", value=date.today().strftime("%Y-%m"))
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
        res = _post("/agent/v1/runs", body, idem or None)
        st.success(res)

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
st.subheader("Runs")

runs = _get("/agent/v1/runs", params={"limit": 50}).get("items", [])
if runs:
    df = pd.DataFrame(runs)
    st.dataframe(df[["run_id", "run_type", "status", "trigger_type", "created_at"]], use_container_width=True)
    run_id = st.text_input("run_id to inspect", value=df.iloc[0]["run_id"])

    if run_id:
        colA, colB = st.columns(2)
        with colA:
            st.markdown("### Tasks")
            tasks = _get("/agent/v1/tasks", params={"run_id": run_id}).get("items", [])
            if tasks:
                st.dataframe(pd.DataFrame(tasks)[["task_name", "status", "error", "created_at"]], use_container_width=True)
        with colB:
            st.markdown("### Logs")
            logs = _get("/agent/v1/logs", params={"run_id": run_id, "limit": 200}).get("items", [])
            if logs:
                st.dataframe(pd.DataFrame(logs)[["ts", "level", "message"]], use_container_width=True)
else:
    st.info("No runs yet. Trigger one above.")

st.divider()
st.subheader("Contract Obligation Cases / Proposals")

current_user = st.text_input("current_user_id (for approvals)", value="checker-001")

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
                                        "user_id": current_user.strip() or None,
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
                                        "user_id": current_user.strip() or None,
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

                evidence_ack = st.checkbox("I have reviewed evidence", value=False)
                note = st.text_input("note (optional)", value="")

                maker = (selected.get("created_by") or "").strip()
                if maker and maker == current_user.strip():
                    st.warning("Maker-checker: you cannot approve your own proposal.")
                    can_act = False
                else:
                    can_act = True

                colX, colY = st.columns(2)
                with colX:
                    if st.button("Approve", disabled=(not can_act) or (not evidence_ack)):
                        try:
                            res = _post(
                                f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                {
                                    "decision": "approve",
                                    "approver_id": current_user.strip(),
                                    "evidence_ack": evidence_ack,
                                    "note": note or None,
                                },
                            )
                            st.success(res)
                        except Exception as e:
                            st.error(e)
                with colY:
                    if st.button("Reject", disabled=(not can_act) or (not evidence_ack)):
                        try:
                            res = _post(
                                f"/agent/v1/contract/proposals/{proposal_id}/approvals",
                                {
                                    "decision": "reject",
                                    "approver_id": current_user.strip(),
                                    "evidence_ack": evidence_ack,
                                    "note": note or None,
                                },
                            )
                            st.success(res)
                        except Exception as e:
                            st.error(e)
