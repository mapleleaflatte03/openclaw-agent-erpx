# Deploy Staging to k3s (production-lite, auto-deploy)

This repo ships a minimal k3s staging deployment for **OpenClaw Agent ERPX (5C)**.

Non-negotiable safety invariant:
- **ERPX core is read-only** from the agent POV.
- Agent writes **auxiliary outputs only**: proposals/approvals/audit/evidence packs and logs.

## A) Topology (from `docker-compose.yml`)

Services to deploy:
- `postgres` (agent DB)
- `redis` (Celery broker/result backend)
- `minio` (+ `minio-init` job) for S3 buckets: attachments/exports/evidence/kb/drop
- `erpx-mock-api` (staging can be self-contained; can be swapped to real ERPX by config)
- `agent-service` (FastAPI)
- `agent-worker-*` (Celery workers)
- `agent-scheduler` (periodic runs)
- `ui` (Streamlit ops UI)
- `ingress` (nginx) routing:
  - `/` -> `ui:8501`
  - `/agent` -> `agent-service:8000`
  - `/erp` -> `erpx-mock-api:8001`

Health endpoints used by probes and smoke:
- `GET /healthz` (agent-service)
- `GET /readyz` (agent-service)

Image build contexts (repo root):
- `services/agent-service/Dockerfile`
- `services/agent-worker/Dockerfile`
- `services/agent-scheduler/Dockerfile`
- `services/erpx-mock-api/Dockerfile`
- `services/ui/Dockerfile`

## B) k8s Manifests Layout

Kustomize:
- Base: `deploy/k8s/base/`
- Staging overlay: `deploy/k8s/overlays/staging/` (namespace: `openclaw-agent-staging`)

Key resources:
- Data core: `postgres`, `redis`, `minio` (StatefulSets + PVCs)
- Jobs:
  - `minio-init` (create buckets)
  - `agent-migrate` (alembic upgrade head; `AUTO_MIGRATE=0` in API pods)
- Apps: `agent-service`, `agent-worker-*`, `agent-scheduler`, `ui`, `erpx-mock-api`

Expected staging URLs (Ingress on node-01):
- UI: `http(s)://<node-01-public-ip>/`
- Agent API: `http(s)://<node-01-public-ip>/agent/v1/...`
- ERPX mock: `http(s)://<node-01-public-ip>/erp/v1/...`

If Ingress is not used yet: use NodePort (staging overlay defaults) or port-forward.

NodePort (staging overlay defaults):
- UI: `http://<node-public-ip>:30851`
- Agent API: `http://<node-public-ip>:30080/agent/v1/...`

CI smoke uses `kubectl port-forward` (does not require Ingress/NodePort to be reachable from the Internet).

## C) Auto-Deploy (GitHub Actions -> k3s staging)

Workflow: `.github/workflows/deploy-staging.yml`
- Triggers: `push` to `main`, and `workflow_dispatch`.
- Runs: lint/tests/openapi export, builds & pushes images, deploys to k3s, then runs smoke.
- Images are pushed to **GHCR** tagged by commit SHA and deployed by SHA:
  - `ghcr.io/mapleleaflatte03/openclaw-agent-erpx/agent-service:<sha>`
  - `ghcr.io/mapleleaflatte03/openclaw-agent-erpx/agent-worker:<sha>`
  - `ghcr.io/mapleleaflatte03/openclaw-agent-erpx/agent-scheduler:<sha>`
  - `ghcr.io/mapleleaflatte03/openclaw-agent-erpx/erpx-mock-api:<sha>`
  - `ghcr.io/mapleleaflatte03/openclaw-agent-erpx/ui:<sha>`

Smoke gate:
- Script: `scripts/smoke_contract_obligation_demo.py` (no UI clicks)
- Verifies: `contract_obligation` run + maker-checker + high-risk 2-step approvals + `evidence_ack` + idempotency.
- Smoke uses `kubectl port-forward` to reach `agent-service`, `ui`, `minio` inside the cluster.

### Required GitHub Environment secrets (Environment: `staging`)

Kubernetes access:
- `STAGING_KUBECONFIG_B64`: base64 of a kubeconfig that can reach the staging cluster API.
- `STAGING_NAMESPACE`: optional override (default `openclaw-agent-staging`).

Runtime secrets (used to create `agent-secrets` in-cluster):
- `STAGING_POSTGRES_PASSWORD`
- `STAGING_AGENT_API_KEY`
- `STAGING_MINIO_ACCESS_KEY`
- `STAGING_MINIO_SECRET_KEY`
- `STAGING_ERPX_TOKEN` (optional; empty is OK for `erpx-mock-api`)
- `STAGING_SMTP_HOST` (optional; leave empty to disable email)
- `STAGING_SMTP_USER` (optional)
- `STAGING_SMTP_PASSWORD` (optional)

Notes:
- No secrets are committed to git.
- No PAT is used; publishing uses `GITHUB_TOKEN` permissions in Actions.
- GHCR packages are **private by default**. The deploy workflow creates an in-cluster pull secret `ghcr-pull`
  and the staging overlay configures the namespace `default` ServiceAccount to use it.
