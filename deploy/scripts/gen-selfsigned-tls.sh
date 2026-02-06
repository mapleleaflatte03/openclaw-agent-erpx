#!/bin/sh
set -eu

NAMESPACE="${NAMESPACE:-openclaw-agent}"
SECRET_NAME="${SECRET_NAME:-openclaw-agent-tls}"
OUT="${OUT:-deploy/k8s/base/tls-secret.yaml}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$tmpdir/tls.key" \
  -out "$tmpdir/tls.crt" \
  -days 3650 \
  -subj "/CN=openclaw-agent/O=OpenClaw"

kubectl -n "$NAMESPACE" create secret tls "$SECRET_NAME" \
  --cert="$tmpdir/tls.crt" \
  --key="$tmpdir/tls.key" \
  --dry-run=client -o yaml > "$OUT"

echo "Wrote: $OUT"
echo "Apply: kubectl apply -f $OUT"

