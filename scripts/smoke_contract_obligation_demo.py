from __future__ import annotations

import argparse
import os
import subprocess
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import boto3
import requests
from reportlab.pdfgen import canvas


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        os.environ.setdefault(k, v)


def _wait_http_ok(url: str, *, timeout_s: int, headers: dict[str, str] | None = None) -> None:
    deadline = time.time() + timeout_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code == 200:
                return
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {url}: {last_err}")


def _make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()


def _make_eml(path: Path, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "buyer@example.local"
    msg["To"] = "ap@acme.example.local"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())


@dataclass(frozen=True)
class Env:
    agent_base_url: str
    ui_base_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket_drop: str
    agent_auth_mode: str
    agent_api_key: str

    @property
    def agent_headers(self) -> dict[str, str]:
        if self.agent_auth_mode == "api_key" and self.agent_api_key:
            return {"X-API-Key": self.agent_api_key}
        return {}


def _env() -> Env:
    return Env(
        agent_base_url=os.getenv("SMOKE_AGENT_BASE_URL", "http://localhost:8000").rstrip("/"),
        ui_base_url=os.getenv("SMOKE_UI_BASE_URL", "http://localhost:8501").rstrip("/"),
        minio_endpoint=os.getenv("SMOKE_MINIO_ENDPOINT", "http://localhost:9000").rstrip("/"),
        minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        minio_bucket_drop=os.getenv("MINIO_BUCKET_DROP", "agent-drop"),
        agent_auth_mode=os.getenv("AGENT_AUTH_MODE", "none").strip(),
        agent_api_key=os.getenv("AGENT_API_KEY", "").strip(),
    )


def _s3_client(env: Env):
    return boto3.client(
        "s3",
        endpoint_url=env.minio_endpoint,
        aws_access_key_id=env.minio_access_key,
        aws_secret_access_key=env.minio_secret_key,
        region_name=os.getenv("MINIO_REGION", "sgp1"),
    )


def _post_json(env: Env, path: str, body: dict[str, Any], *, idem: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **env.agent_headers}
    if idem:
        headers["Idempotency-Key"] = idem
    r = requests.post(f"{env.agent_base_url}{path}", json=body, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def _get_json(env: Env, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.get(f"{env.agent_base_url}{path}", params=params, headers=env.agent_headers, timeout=15)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test for contract_obligation demo (5C).")
    ap.add_argument("--up", action="store_true", help="Run `docker compose up -d` before testing.")
    ap.add_argument("--build", action="store_true", help="Add `--build` to docker compose up.")
    ap.add_argument("--timeout", type=int, default=120, help="Overall timeout (seconds) for waiting run completion.")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    _load_dotenv(repo_root / ".env")
    env = _env()

    if args.up:
        cmd = ["docker", "compose", "up", "-d"]
        if args.build:
            cmd.append("--build")
        subprocess.run(cmd, cwd=str(repo_root), check=True)

    # Services ready
    _wait_http_ok(f"{env.agent_base_url}/healthz", timeout_s=60, headers=env.agent_headers)
    _wait_http_ok(f"{env.agent_base_url}/readyz", timeout_s=60, headers=env.agent_headers)
    _wait_http_ok(f"{env.ui_base_url}/", timeout_s=60)
    _wait_http_ok(f"{env.minio_endpoint}/minio/health/ready", timeout_s=60)

    # Generate sources
    tmp = Path("/tmp/accounting-agent-smoke")
    tmp.mkdir(parents=True, exist_ok=True)
    contract_pdf = tmp / "contract_A.pdf"
    email_eml = tmp / "thread_A.eml"
    _make_pdf(
        contract_pdf,
        [
            "Milestone payment: 30% within 10 days.",
            "Late payment penalty: 0.05% per day if late.",
        ],
    )
    _make_eml(email_eml, "Re: HD-ACME-2026-0001", "Early payment discount: 2% if paid within 5 days.")

    # Upload to MinIO drop (so worker containers can download via s3:// URIs).
    s3 = _s3_client(env)
    ts = int(time.time())
    key_pdf = f"drop/attachments/{ts}_{contract_pdf.name}"
    key_eml = f"drop/attachments/{ts}_{email_eml.name}"
    s3.upload_file(str(contract_pdf), env.minio_bucket_drop, key_pdf)
    s3.upload_file(str(email_eml), env.minio_bucket_drop, key_eml)
    pdf_uri = f"s3://{env.minio_bucket_drop}/{key_pdf}"
    eml_uri = f"s3://{env.minio_bucket_drop}/{key_eml}"

    # Trigger run
    idem_run = f"smoke-contract-obligation-{ts}"
    res = _post_json(
        env,
        "/agent/v1/runs",
        {
            "run_type": "contract_obligation",
            "trigger_type": "manual",
            "requested_by": "maker-001",
            "payload": {"contract_files": [pdf_uri], "email_files": [eml_uri]},
        },
        idem=idem_run,
    )
    run_id = res["run_id"]
    print(f"run_id={run_id}")

    # Poll completion
    deadline = time.time() + int(args.timeout)
    status = None
    run: dict[str, Any] | None = None
    while time.time() < deadline:
        run = _get_json(env, f"/agent/v1/runs/{run_id}")
        status = run.get("status")
        if status in {"success", "failed"}:
            break
        time.sleep(1.0)
    if status != "success":
        raise RuntimeError(f"run did not succeed: status={status} run={run}")

    cursor_out = run.get("cursor_out") or {}
    case_id = cursor_out.get("case_id")
    if not case_id:
        raise RuntimeError(f"missing cursor_out.case_id: {cursor_out}")

    proposals = _get_json(env, f"/agent/v1/contract/cases/{case_id}/proposals").get("items", [])
    if not proposals:
        raise RuntimeError("no proposals created")

    # Pick a high-risk proposal (approvals_required=2).
    high = next((p for p in proposals if int(p.get("approvals_required") or 0) == 2), None)
    if not high:
        raise RuntimeError(f"no high-risk proposal found. proposals={proposals}")

    proposal_id = high["proposal_id"]
    maker = (high.get("created_by") or "").strip()
    if maker != "maker-001":
        raise RuntimeError(f"unexpected maker: {maker}")
    print(f"high_risk_proposal_id={proposal_id} type={high.get('proposal_type')} tier={high.get('tier')}")

    # Maker-checker: self approve blocked
    r = requests.post(
        f"{env.agent_base_url}/agent/v1/contract/proposals/{proposal_id}/approvals",
        json={"decision": "approve", "approver_id": "maker-001", "evidence_ack": True},
        headers={"Content-Type": "application/json", **env.agent_headers},
        timeout=15,
    )
    if r.status_code != 409:
        raise RuntimeError(f"expected 409 for self-approve, got {r.status_code}: {r.text}")

    # Approve #1 -> pending_l2
    res1 = _post_json(
        env,
        f"/agent/v1/contract/proposals/{proposal_id}/approvals",
        {"decision": "approve", "approver_id": "checker-001", "evidence_ack": True},
        idem=f"smoke-approval-1-{ts}",
    )
    if res1.get("proposal_status") != "pending_l2":
        raise RuntimeError(f"expected pending_l2 after first approval: {res1}")
    approval_id_1 = res1["approval_id"]

    # Idempotency repeat -> same approval_id/status
    res1b = _post_json(
        env,
        f"/agent/v1/contract/proposals/{proposal_id}/approvals",
        {"decision": "approve", "approver_id": "checker-001", "evidence_ack": True},
        idem=f"smoke-approval-1-{ts}",
    )
    if res1b.get("approval_id") != approval_id_1:
        raise RuntimeError(f"idempotency mismatch: {res1b} vs {res1}")

    # Approve #2 -> approved
    res2 = _post_json(
        env,
        f"/agent/v1/contract/proposals/{proposal_id}/approvals",
        {"decision": "approve", "approver_id": "checker-002", "evidence_ack": True},
        idem=f"smoke-approval-2-{ts}",
    )
    if res2.get("proposal_status") != "approved":
        raise RuntimeError(f"expected approved after second approval: {res2}")

    approvals = _get_json(env, f"/agent/v1/contract/proposals/{proposal_id}/approvals").get("items", [])
    if len(approvals) < 2:
        raise RuntimeError(f"expected >=2 approvals, got {approvals}")
    print("ok: approvals=2, status=approved, ui=reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
