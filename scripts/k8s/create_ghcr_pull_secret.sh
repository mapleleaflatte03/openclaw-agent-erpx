#!/usr/bin/env bash
# scripts/k8s/create_ghcr_pull_secret.sh
# Creates a Kubernetes docker-registry secret for pulling images from GHCR.
#
# Usage:
#   GHCR_USERNAME=xxx GHCR_TOKEN=xxx ./scripts/k8s/create_ghcr_pull_secret.sh [namespace]
#
# Environment:
#   GHCR_USERNAME  – GitHub username or org (required)
#   GHCR_TOKEN     – GitHub PAT with read:packages scope (required)
#   GHCR_EMAIL     – email for docker-registry (default: ci@openclaw.local)
#
# The secret is named "ghcr-pull" to match imagePullSecrets in ServiceAccount.

set -euo pipefail

NAMESPACE="${1:-openclaw-agent-staging}"
SECRET_NAME="ghcr-pull"
REGISTRY="ghcr.io"

# --- Fail-fast: require env vars ---
: "${GHCR_USERNAME:?ERROR: GHCR_USERNAME is not set}"
: "${GHCR_TOKEN:?ERROR: GHCR_TOKEN is not set}"
GHCR_EMAIL="${GHCR_EMAIL:-ci@openclaw.local}"

echo "[INFO] Creating namespace '$NAMESPACE' if not exists..."
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

echo "[INFO] Creating/updating docker-registry secret '$SECRET_NAME' in '$NAMESPACE'..."
kubectl -n "$NAMESPACE" create secret docker-registry "$SECRET_NAME" \
  --docker-server="$REGISTRY" \
  --docker-username="$GHCR_USERNAME" \
  --docker-password="$GHCR_TOKEN" \
  --docker-email="$GHCR_EMAIL" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "[INFO] Verifying secret exists..."
kubectl -n "$NAMESPACE" get secret "$SECRET_NAME" -o name

echo "[INFO] Verifying ServiceAccount 'default' has imagePullSecrets..."
kubectl -n "$NAMESPACE" get sa default -o jsonpath='{.imagePullSecrets[*].name}' 2>/dev/null || true
echo ""

echo "[OK] Done. Secret '$SECRET_NAME' is ready in namespace '$NAMESPACE'."
