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

    idem = st.text_input("Idempotency-Key (optional)", value="")
    if st.button("Run"):
        res = _post("/agent/v1/runs", {"run_type": run_type, "trigger_type": "manual", "payload": payload}, idem or None)
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

