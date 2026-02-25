#!/bin/sh
set -eu

NAMESPACE="${NAMESPACE:-accounting-agent}"
OUT="${OUT:-deploy/k8s/base/secret.yaml}"

rand() {
  # 32 chars urlsafe-ish
  openssl rand -base64 48 | tr -d '\n' | tr '+/' 'ab' | cut -c1-32
}

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(rand)}"
AGENT_API_KEY="${AGENT_API_KEY:-$(rand)}"
ERPX_TOKEN="${ERPX_TOKEN:-$(rand)}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minio$(rand | cut -c1-12)}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-$(rand)}"

# Keep MINIO_ROOT_* aligned with client creds for simplicity (single-user demo).
MINIO_ROOT_USER="${MINIO_ROOT_USER:-$MINIO_ACCESS_KEY}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$MINIO_SECRET_KEY}"

cat > "$OUT" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: agent-secrets
  namespace: ${NAMESPACE}
type: Opaque
stringData:
  POSTGRES_DB: "agent"
  POSTGRES_USER: "agent"
  POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"

  AGENT_DB_DSN: "postgresql+psycopg://agent:${POSTGRES_PASSWORD}@postgres:5432/agent"

  ERPX_TOKEN: "${ERPX_TOKEN}"
  AGENT_API_KEY: "${AGENT_API_KEY}"

  MINIO_ACCESS_KEY: "${MINIO_ACCESS_KEY}"
  MINIO_SECRET_KEY: "${MINIO_SECRET_KEY}"

  MINIO_ROOT_USER: "${MINIO_ROOT_USER}"
  MINIO_ROOT_PASSWORD: "${MINIO_ROOT_PASSWORD}"

  SMTP_HOST: ""
  SMTP_PORT: "587"
  SMTP_USER: ""
  SMTP_PASSWORD: ""
  SMTP_FROM: "accounting@example.local"
  SMTP_TLS: "true"
EOF

echo "Wrote: $OUT"
echo "Apply: kubectl apply -f $OUT"
