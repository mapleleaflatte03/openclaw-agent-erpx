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

### LLM Integration (Optional)

The agent flows (Q&A, journal suggestion, soft-checks) support real LLM
calls via the DigitalOcean OpenAI-compatible endpoint.  By default,
`USE_REAL_LLM=false` and all flows use rule-based logic.

To enable:
```bash
# In .env
USE_REAL_LLM=true
DO_AGENT_BASE_URL=https://your-agent.agents.do-ai.run
DO_AGENT_API_KEY=your-api-key
DO_AGENT_MODEL=OpenAI GPT-oss-120b
```

### Using Real VN Data (Optional)

The pipeline can process real Vietnamese accounting documents alongside
the built-in synthetic data generator.

```bash
# 1. Prepare: generate fixtures + convert downloaded Kaggle data
python scripts/prepare_real_vn_data.py \
  --source-dir data/real_vn/mc_ocr/ \
  --output-dir data/real_vn/prepared/ \
  --max-files 20

# 2. Upload to MinIO in real or mix mode
python scripts/upload_minio_simulate_erp.py \
  --mode real --real-data-dir data/real_vn/prepared/ \
  --interval 30 --cycles 5

# 3. Observe in UI: Agent Command Center, Chứng từ, Bút toán tabs
```

Supported data sources (see `scripts/prepare_real_vn_data.py` for details):
- Kaggle MC_OCR 2021 (Vietnamese receipts)
- Kaggle Receipt OCR VN
- Kaggle Appen VN OCR (11 doc categories, CC BY-SA 4.0)
- GDT e-invoices (NĐ 123/2020)
- TT133/2016 regulation excerpts

> **⚠️ Never commit real data files to the repo.**

### Lint/Test
```bash
make lint
make test
```
