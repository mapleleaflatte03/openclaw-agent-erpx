# Demo: Contract Obligation Agent (5C) (10 minutes)

Scope/safety (non-negotiable):
- ERPX core: **read-only** (agent does not post entries, does not write accounting numbers).
- Agent writes **auxiliary outputs only**: `proposals` (drafts), `approvals`, `audit log`, `evidence pack` (MinIO), plus run/task logs.

## 0) Start Local Stack (Docker Compose)

```bash
cd /root/openclaw-agent-erpx
cp -n .env.example .env
docker compose up -d --build
```

Quick checks:
```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/readyz | jq .
curl -fsS http://localhost:8501 >/dev/null && echo "ui ok"
```

## 1) Generate Demo Source Files (PDF + EML)

This generates 2 sets:
- Set A (high-confidence): yields Tier1 (milestone) + Tier2 high-risk (late payment penalty) + approvals demo.
- Set B (low-confidence): yields Tier3 `missing_data`.

```bash
. .venv/bin/activate
python - <<'PY'
from email.message import EmailMessage
from pathlib import Path
from reportlab.pdfgen import canvas

out = Path("/tmp/openclaw-demo")
out.mkdir(parents=True, exist_ok=True)

def make_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()

def make_eml(path: Path, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "buyer@example.local"
    msg["To"] = "ap@acme.example.local"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())

pdf_a = out / "contract_A.pdf"
eml_a = out / "thread_A.eml"
make_pdf(pdf_a, [
    "Milestone payment: 30% within 10 days.",
    "Late payment penalty: 0.05% per day if late.",
])
make_eml(eml_a, "Re: HD-ACME-2026-0001", "Early payment discount: 2% if paid within 5 days.")

pdf_b = out / "contract_B_low_conf.pdf"
make_pdf(pdf_b, ["Payment terms: to be discussed."])

print("Generated:")
print(" ", pdf_a)
print(" ", eml_a)
print(" ", pdf_b)
PY
```

## 2) Upload Sources to MinIO Drop (via UI)

Open UI: `http://localhost:8501`

In **Upload Drop File (Event Trigger)**:
- upload `/tmp/openclaw-demo/contract_A.pdf` (any drop_type is OK)
- upload `/tmp/openclaw-demo/thread_A.eml`
- copy returned `file_uri` (looks like `s3://agent-drop/drop/...`)

## 3) Trigger Run (Maker)

In UI: **Trigger Manual Run**
- `requested_by`: `maker-001`
- `run_type`: `contract_obligation`
- `contract_files`: paste the `s3://...contract_A.pdf` URI
- `email_files`: paste the `s3://...thread_A.eml` URI
- click **Run**

Wait until run status is `success` in **Runs** section.

## 4) Show 5C.1 (Tier Gating) Output

In UI: **Contract Obligation Cases / Proposals**
- pick the newest `case_id`
- show **Obligations** table: `risk_level`, `confidence`
- show **Proposals** table: `proposal_type`, `tier`, `risk_level`, `status`, `created_by`, `approvals_approved/required`

What to highlight:
- Tier 1 (milestone) creates **draft**:
  - `proposal_type=reminder`, `tier=1`, `status=draft`
  - `proposal_type=accrual_template` only for `milestone_payment` Tier1 (still draft, auxiliary only)
- Tier 2 (penalty) creates **summary/confirm** only:
  - `proposal_type=review_confirm`, `tier=2`
  - **NO** `accrual_template` for Tier2/Tier3
- Proposal `details` includes:
  - `evidence_pack_uri` (MinIO object) and `conflicts` (if any)
  - note: "Draft outside ERPX core..."

## 5) Show 5C.2 Maker-Checker + 2-Step High-Risk Approval

In UI (right side):
- set `current_user_id = maker-001`
  - show UI warning: maker-checker blocks self-approve
- set `current_user_id = checker-001`
  - select **high-risk** proposal (risk_level=high, approvals_required=2)
  - tick **I have reviewed evidence**
  - click **Approve**
  - show response: `proposal_status=pending_l2`, approvals `1/2`
- set `current_user_id = checker-002` (must be different user)
  - tick evidence checkbox again
  - click **Approve**
  - show response: `proposal_status=approved`, approvals `2/2`

Optional (Tier 3):
- upload `/tmp/openclaw-demo/contract_B_low_conf.pdf`
- trigger another run (requested_by can be `maker-001`)
- show Tier3 output: `proposal_type=missing_data`, `tier=3`

## API Endpoints (for backup/demo without UI)

Agent (aux outputs):
- `POST /agent/v1/runs` (trigger)
- `GET /agent/v1/runs/{run_id}` (poll status + `cursor_out.case_id`)
- `GET /agent/v1/contract/cases`
- `GET /agent/v1/contract/cases/{case_id}/obligations`
- `GET /agent/v1/contract/cases/{case_id}/proposals`
- `GET /agent/v1/contract/proposals/{proposal_id}/approvals`
- `POST /agent/v1/contract/proposals/{proposal_id}/approvals` (maker-checker + evidence_ack + 2-step high-risk)
- `GET /agent/v1/contract/audit` (append-only audit trail)

ERPX (read-only):
- `GET /erp/v1/*` (mock in local compose: `http://localhost:8001/erp/v1/...`)

