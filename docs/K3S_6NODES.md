# k3s Install on 6 Nodes (leviathan-data x6)

Target OS: Ubuntu 22.04 LTS. Region: SGP1. 6 nodes: node-01..node-06.

This guide assumes each node has:
- Public NIC: `eth0`
- Private/VPC NIC: `eth1` (DigitalOcean VPC default)

## 0) Do First (Firewall/VPC)
1. Put all 6 nodes in the same **VPC** (private network).
2. Firewall (minimum):
   - Public inbound:
     - `443/tcp` -> node-01 only
     - `22/tcp` -> your admin IPs (or bastion)
   - Private/VPC inbound (between nodes):
     - `6443/tcp` (k3s API server) -> node-01
     - allow all node-to-node inside VPC (recommended)

## 1) Base Setup (all nodes)
Run on **each** node:
```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates jq
sudo systemctl disable --now ufw || true
```

## 2) Install k3s Server on node-01
On **node-01**:
```bash
export NODE_PRIVATE_IP="$(ip -4 addr show dev eth1 | awk '/inet /{print $2}' | cut -d/ -f1)"

curl -sfL https://get.k3s.io | sudo sh -s - server \
  --cluster-init \
  --disable traefik \
  --disable servicelb \
  --flannel-iface=eth1 \
  --node-ip="${NODE_PRIVATE_IP}" \
  --advertise-address="${NODE_PRIVATE_IP}" \
  --write-kubeconfig-mode=644

sudo kubectl get nodes -o wide
sudo cat /var/lib/rancher/k3s/server/node-token
```

## 3) Join Agents on node-02..node-06
On **node-02..node-06** (each node), set the node-01 private IP and token:
```bash
export SERVER_PRIVATE_IP="REPLACE_WITH_NODE01_PRIVATE_IP"
export K3S_TOKEN="REPLACE_WITH_NODE_TOKEN"
export NODE_PRIVATE_IP="$(ip -4 addr show dev eth1 | awk '/inet /{print $2}' | cut -d/ -f1)"

curl -sfL https://get.k3s.io | sudo sh -s - agent \
  --server "https://${SERVER_PRIVATE_IP}:6443" \
  --token "${K3S_TOKEN}" \
  --flannel-iface=eth1 \
  --node-ip="${NODE_PRIVATE_IP}"
```

Verify from **node-01**:
```bash
sudo kubectl get nodes -o wide
```

## 4) Label Nodes (role-based scheduling)
On **node-01**:
```bash
sudo kubectl label node node-01 role=control-ingress --overwrite
sudo kubectl label node node-02 role=data-core --overwrite
sudo kubectl label node node-03 role=worker pool-ocr=true --overwrite
sudo kubectl label node node-04 role=worker pool-ocr=true pool-export=true --overwrite
sudo kubectl label node node-05 role=worker pool-io=true pool-index=true --overwrite
sudo kubectl label node node-06 role=observability standby-worker=true pool-standby=true --overwrite

sudo kubectl get nodes --show-labels
```

## 5) Install ingress-nginx (hostNetwork on node-01)
On **node-01**:
```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.hostNetwork=true \
  --set controller.service.type=ClusterIP \
  --set controller.nodeSelector.role=control-ingress \
  --set controller.admissionWebhooks.patch.nodeSelector.role=control-ingress \
  --set controller.extraArgs.enable-ssl-passthrough="" \
  --set controller.config.use-forwarded-headers="true"

sudo kubectl -n ingress-nginx get pods -o wide
```

## 6) (Optional) Metrics Server (for HPA)
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system rollout status deploy/metrics-server
```

Next: deploy the stack via `deploy/k8s/` (see `docs/DEPLOY_K8S.md`).
