# Runbook — OpenClaw Agent ERPX Operations

## 1. Backup & Restore

### PostgreSQL

```bash
# Backup (daily cron recommended)
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U openclaw -Fc openclaw_agent \
  > /backups/pg/openclaw_agent_$(date +%Y%m%d_%H%M).dump

# Restore to clean DB
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h postgres -U openclaw -d openclaw_agent \
  --clean --if-exists /backups/pg/openclaw_agent_YYYYMMDD_HHMM.dump
```

- Schedule: daily at 02:00 UTC (k8s CronJob or host cron).
- Retention: 7 daily + 4 weekly. Prune older via `find /backups/pg -mtime +30 -delete`.

### MinIO (S3-compatible)

```bash
# Mirror all buckets to local backup
mc alias set agent http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
mc mirror --overwrite agent/ /backups/minio/

# Restore a bucket
mc mirror --overwrite /backups/minio/evidence agent/evidence
```

- Buckets: `attachments`, `exports`, `evidence`, `kb`, `drop`.
- Retention: keep evidence indefinitely; prune `exports`/`drop` older than 90 days.

## 2. Retention & Cleanup

| Data              | Location          | Retention    | Cleanup Command                                              |
| ----------------- | ----------------- | ------------ | ------------------------------------------------------------ |
| Agent run outputs  | Postgres           | 365 days     | `DELETE FROM agent_runs WHERE created_at < now()-'365d'`     |
| Reports           | `reports/`         | 90 days      | `find reports/ -mtime +90 -delete`                          |
| Benchmark results | `reports/benchmark`| 30 days      | `find reports/benchmark -mtime +30 -delete`                 |
| MinIO exports     | `exports` bucket   | 90 days      | `mc rm --older-than 90d agent/exports --recursive --force`  |
| MinIO drop        | `drop` bucket      | 7 days       | `mc rm --older-than 7d agent/drop --recursive --force`      |
| Logs              | stdout/stderr      | k8s default  | Managed by k8s log rotation / Loki                          |

## 3. Disk Full (>80%)

**Alert**: Monitor node disk usage; alert at 80%.

**Triage checklist**:
1. Check which PVC is full: `kubectl get pvc -n openclaw-agent-staging`
2. Postgres: run retention cleanup, `VACUUM FULL`.
3. MinIO: prune old exports/drop buckets.
4. Reports: delete old benchmark reports.
5. If still full: expand PVC (`kubectl edit pvc`) or add node storage.

## 4. Queue Backlog

**Symptom**: Celery tasks pending > 100 or task age > 30 min.

```bash
# Check queue depth
kubectl exec -n openclaw-agent-staging deploy/redis -- redis-cli LLEN celery

# Check active workers
kubectl exec -n openclaw-agent-staging deploy/agent-worker -- celery -A openclaw_agent.agent_worker.tasks inspect active

# If stuck: restart workers (rolling)
kubectl rollout restart deploy/agent-worker -n openclaw-agent-staging
```

**Rate limit**: ERPX API is capped at 10 req/s with token-bucket. If backlog grows due to rate limiting, do NOT increase the limit — wait for natural drain.

## 5. On-Call Health Checks

```bash
NS=openclaw-agent-staging

# 1. All pods running?
kubectl get pods -n $NS

# 2. Agent service healthy?
kubectl exec -n $NS deploy/agent-service -- curl -sf http://localhost:8000/healthz
kubectl exec -n $NS deploy/agent-service -- curl -sf http://localhost:8000/readyz

# 3. Postgres accessible?
kubectl exec -n $NS deploy/postgres -- pg_isready -U openclaw

# 4. Redis accessible?
kubectl exec -n $NS deploy/redis -- redis-cli ping

# 5. MinIO accessible?
kubectl exec -n $NS deploy/minio -- curl -sf http://localhost:9000/minio/health/live

# 6. Recent runs succeeding?
kubectl exec -n $NS deploy/agent-service -- curl -sf http://localhost:8000/agent/v1/runs?limit=5
```

## 6. Deploy Verification & Rollout

```bash
# Apply staging overlay
kubectl apply -k deploy/k8s/overlays/staging-single  # or staging-6nodes
kubectl rollout status deploy/agent-service -n $NS --timeout=120s
kubectl rollout status deploy/agent-worker -n $NS --timeout=120s

# Smoke test
./scripts/smoke_e2e.sh

# Rollback if broken
kubectl rollout undo deploy/agent-service -n $NS
kubectl rollout undo deploy/agent-worker -n $NS
```

## 7. Secret Rotation

### Postgres password
1. Update secret in GitHub Actions environment (`STAGING_POSTGRES_PASSWORD`).
2. Update k8s secret: `kubectl create secret generic agent-secrets -n $NS --from-literal=POSTGRES_PASSWORD=<new> --dry-run=client -o yaml | kubectl apply -f -`
3. Restart pods: `kubectl rollout restart deploy -n $NS`

### MinIO keys
1. Update `STAGING_MINIO_ACCESS_KEY` / `STAGING_MINIO_SECRET_KEY` in Actions.
2. Update k8s secret, restart pods.

### ERPX token
1. Update `STAGING_ERPX_TOKEN` in Actions.
2. Update k8s secret, restart `agent-worker` and `agent-scheduler`.

### GHCR pull secret
1. Re-run `scripts/k8s/create_ghcr_pull_secret.sh` with new credentials.

## 8. Audit Immutability

- App layer: no UPDATE/DELETE code paths for `agent_audit_log` table.
- Postgres trigger: `prevent_audit_mutation` blocks UPDATE/DELETE on `agent_audit_log`.
- To verify: `SELECT * FROM pg_trigger WHERE tgname = 'prevent_audit_mutation';`
- **Never disable** the trigger in production.
