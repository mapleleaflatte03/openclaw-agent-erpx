# Architecture (1-page)

## Objective
Run an **auxiliary accounting agent** side-by-side with ERPX. The agent is **read-only** for accounting data and only writes **auxiliary outputs** (attachments/exports/exception lists/reminder logs/close checklist/evidence packs/KB docs/logs).

## Components
- **ERPX Core / ERPX Mock** (`erpx-mock-api`)
  - Read-only business data endpoints: `/erp/v1/*`
- **Agent API / Orchestrator** (`agent-service`)
  - REST API for operations/UI: `/agent/v1/*`
  - Creates runs/tasks, enqueues work, exposes metrics
- **Workers** (`agent-worker`)
  - Celery workers consuming Redis queues, executing workflows (OCR/ETL/I-O/export/index + contract obligation)
- **Scheduler** (`agent-scheduler`)
  - Cron-style triggers + polling MinIO drop buckets (event triggers)
- **Data**
  - Postgres: all `agent_*` tables (runs/tasks/logs/outputs/feedback)
  - Redis: Celery broker/result + queue backlog
  - MinIO (S3): attachments/exports/evidence packs/KB text + drop buckets
- **UI** (`ui`)
  - Streamlit ops UI for runs/tasks/logs/outputs + upload to drop bucket
- **Observability**
  - Prometheus + Grafana + Loki (kube-prometheus-stack + loki)

## High-level Data Flow
1. Trigger (schedule/event/manual) creates `agent_runs` + initial tasks
2. `agent-worker` runs workflow steps (OCR/parse/match/export/notify/pack/index)
3. Outputs written to:
   - Postgres (`agent_attachments`, `agent_exports`, `agent_exceptions`, ...)
   - MinIO objects (files) with checksums
4. Status/logs written to `agent_tasks` + `agent_logs`
5. UI queries `/agent/v1/*` to show history and outputs

## Contract Obligation Agent (5C)
- **Scope**: ERPX **read-only**; agent chỉ tạo **draft ngoài luồng** (proposals/approvals/audit/evidence packs).
- **3-tier gating (Tier1/Tier2/Tier3)** dựa trên required fields + evidence strength + conflict handling:
  - Tier 1: draft `reminder` (+ `accrual_template` chỉ cho milestone), có evidence pack
  - Tier 2: `review_confirm` (summary + confirm quick), không tạo template bút toán
  - Tier 3: `missing_data`, không suy diễn
- **Maker-checker 2 lớp** cho `risk_level=high`: người tạo không được duyệt; cần 2 approver khác nhau; bắt buộc tick `evidence_ack=true`.

## 6-node Layout (leviathan-data x6)
Each node: 4 vCPU / 16GB / 200GB, Ubuntu 22.04, SGP1.

- **node-01 (control + ingress + API + UI)**
  - k3s server (control plane)
  - ingress-nginx (hostNetwork, binds 443 on node-01)
  - `agent-service`, `ui`, `erpx-mock-api`
  - `agent-scheduler`

- **node-02 (data core)**
  - Postgres (PVC on node-02)
  - Redis (PVC on node-02)
  - MinIO (PVC on node-02)

- **node-03 (worker OCR/ETL #1)**
  - `agent-worker-ocr` (queue: `ocr`, concurrency 1)

- **node-04 (worker OCR/ETL #2 + export)**
  - `agent-worker-ocr` (queue: `ocr`, concurrency 1)
  - `agent-worker-export` (queue: `export`, concurrency 1)

- **node-05 (worker I/O + pack/index)**
  - `agent-worker-io` (queues: `io,index,default`, concurrency 2, scaled by replicas)

- **node-06 (observability + standby scale)**
  - kube-prometheus-stack + loki (nodeSelector)
  - optional `agent-worker-standby` scaled to 0 by default

## Network / Firewall (minimum)
- Public inbound:
  - 443/tcp -> node-01 only (ingress)
  - 22/tcp -> node(s) you SSH to, restricted by your IP (or bastion)
- Private VPC (node-to-node):
  - k3s: 6443/tcp (API server)
  - overlay network: flannel (k3s manages)
  - data-plane (internal only): Postgres 5432, Redis 6379, MinIO 9000/9001
- No public exposure for Postgres/Redis/MinIO.
