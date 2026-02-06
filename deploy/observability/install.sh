#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

kubectl apply -f deploy/observability/namespace.yaml

# Loki + promtail (logs)
kubectl apply -f deploy/observability/loki.yaml
kubectl apply -f deploy/observability/promtail.yaml

# Prometheus + Grafana + Alertmanager
if ! command -v helm >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace observability --create-namespace \
  -f deploy/observability/helm/kube-prometheus-stack-values.yaml

# Datasources for Grafana (fixed UID for dashboards)
kubectl apply -f deploy/observability/grafana-datasources.yaml

# Agent metrics + dashboards + alerts
kubectl apply -f deploy/observability/agent-servicemonitor.yaml
kubectl apply -f deploy/observability/alerts/agent-alerts.yaml
kubectl apply -f deploy/observability/dashboards/agent-dashboard-configmap.yaml

echo "Observability installed."
echo "Grafana (port-forward): kubectl -n observability port-forward svc/kube-prometheus-stack-grafana 3000:80"

