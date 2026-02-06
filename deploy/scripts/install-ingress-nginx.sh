#!/bin/sh
set -eu

NAMESPACE="${NAMESPACE:-ingress-nginx}"
DEFAULT_CERT_NS="${DEFAULT_CERT_NS:-openclaw-agent}"
DEFAULT_CERT_NAME="${DEFAULT_CERT_NAME:-openclaw-agent-tls}"

if ! command -v helm >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace "$NAMESPACE" --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.hostNetwork=true \
  --set controller.service.type=ClusterIP \
  --set controller.nodeSelector.role=control-ingress \
  --set controller.admissionWebhooks.patch.nodeSelector.role=control-ingress \
  --set controller.extraArgs.default-ssl-certificate="${DEFAULT_CERT_NS}/${DEFAULT_CERT_NAME}" \
  --set controller.config.use-forwarded-headers="true"

kubectl -n "$NAMESPACE" rollout status ds/ingress-nginx-controller

