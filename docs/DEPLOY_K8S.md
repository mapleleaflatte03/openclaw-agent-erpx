# Deploy to k3s (leviathan-data x6)

Assumptions:
- k3s cluster is installed and nodes are labeled as in `docs/K3S_6NODES.md`.
- You will load images into k3s (no external registry required).

## 1) Build + Load Images to All Nodes

### Do first
- Ensure your build host has `docker`, `scp`, `ssh`.
- Ensure each node has k3s installed (so `sudo k3s ctr` works).

### Commands (run from this repo root)
```bash
cd openclaw-agent-erpx

# Replace with your SSH targets (can be IPs or hostnames).
./scripts/k8s/build_and_load_images.sh node-01 node-02 node-03 node-04 node-05 node-06
```

### Verify (on node-01)
```bash
sudo k3s ctr images ls | grep openclaw-agent-erpx
```

## 2) Create Namespace + Secrets + TLS

### Commands (on node-01)
```bash
cd openclaw-agent-erpx

kubectl apply -f deploy/k8s/overlays/prod/namespace.yaml

chmod +x deploy/scripts/gen-secrets.sh deploy/scripts/gen-selfsigned-tls.sh deploy/scripts/install-ingress-nginx.sh
./deploy/scripts/gen-secrets.sh
./deploy/scripts/gen-selfsigned-tls.sh

kubectl apply -f deploy/k8s/base/secret.yaml
kubectl apply -f deploy/k8s/base/tls-secret.yaml
```

### Verify
```bash
kubectl -n openclaw-agent get secret agent-secrets
```

## 3) Deploy Data Core (Postgres/Redis/MinIO) on node-02

### Commands
```bash
kubectl apply -k deploy/k8s/overlays/prod

# Initialize MinIO buckets
kubectl -n openclaw-agent wait --for=condition=complete job/minio-init --timeout=300s
```

### Verify
```bash
kubectl -n openclaw-agent get pods -o wide
kubectl -n openclaw-agent get svc
```

## 4) Run DB Migrations

### Commands
```bash
kubectl -n openclaw-agent wait --for=condition=complete job/agent-migrate --timeout=300s
```

### Verify
```bash
kubectl -n openclaw-agent logs job/agent-migrate
```

## 5) Deploy Apps + Workers

### Commands
```bash
kubectl apply -k deploy/k8s/overlays/prod
```

### Verify
```bash
kubectl -n openclaw-agent get pods -o wide
kubectl -n openclaw-agent port-forward svc/agent-service 8000:8000
```
In another terminal:
```bash
curl -fsS http://127.0.0.1:8000/healthz
```

## 6) Install Ingress (443 on node-01)

### Commands
```bash
./deploy/scripts/install-ingress-nginx.sh
kubectl apply -k deploy/k8s/overlays/prod
```

### Verify
1. Ensure firewall allows `443/tcp` to node-01.
2. Visit: `https://<node-01-public-ip>/` (self-signed cert warning is expected).

## 7) Scale Workers During Peak
```bash
chmod +x scripts/k8s/scale_workers.sh
OCR=3 IO=6 ./scripts/k8s/scale_workers.sh

# Use node-06 as standby compute
STANDBY=1 ./scripts/k8s/scale_workers.sh
```
