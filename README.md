# OpenClaw Agent for ERPX (Auxiliary Accounting Workflows)

This repo implements the design doc:
`/root/tai_liệu_thiết_kế_open_claw_agent_hỗ_trợ_nghiệp_vụ_kế_toan_ngoai_luồng_ghi_sổ_trong_erpx.md`

Key constraints (enforced by design):
- The agent is **read-only** for accounting data. It does **not** post journals, change amounts/accounts, or close/open periods.
- The agent only writes auxiliary outputs: attachments, exports, exception lists, reminder logs, close checklist tasks, evidence packs, KB docs, logs.

## Local Demo (Docker Compose)

### 1) What to do first
1. Install Docker + Docker Compose v2.
2. Create local env file.

### 2) Commands
```bash
cd openclaw-agent-erpx
cp .env.example .env
docker compose up -d --build
```

### 3) Verify
```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8001/healthz
curl -fsS http://localhost:8000/agent/v1/runs | head
```

Open UI:
- `http://localhost:8501`

Trigger a manual run (VAT export example):
```bash
curl -fsS -X POST http://localhost:8000/agent/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: vat_list:2026-01' \
  -d '{"run_type":"tax_export","trigger_type":"manual","payload":{"period":"2026-01"}}' | jq .
```

## k3s on 6 Nodes (leviathan-data x6)
- Architecture summary: `docs/ARCHITECTURE.md`
- Workflow/skills mapping: `docs/WORKFLOWS.md`
- Step-by-step k3s install (6 nodes): `docs/K3S_6NODES.md`
- Kubernetes manifests: `deploy/k8s/`
- Observability (Prometheus/Grafana/Loki): `deploy/observability/`

## Dev

### Setup
```bash
python3 -m pip install -U pip
python3 -m pip install -e '.[dev,ui]'
```

### Lint/Test
```bash
make lint
make test
```
