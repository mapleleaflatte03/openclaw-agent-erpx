#!/bin/sh
set -eu

VERSION="${VERSION:-0.1.0}"
IMG_PREFIX="${IMG_PREFIX:-accounting-agent-layer}"
TAR="${TAR:-/tmp/accounting-agent-images.tar}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required on the build host" >&2
  exit 1
fi

if [ "${#}" -lt 1 ]; then
  echo "Usage: $0 node-01 node-02 ... (ssh targets)" >&2
  exit 1
fi

set -x
docker build -t "${IMG_PREFIX}/agent-service:${VERSION}" -f services/agent-service/Dockerfile .
docker build -t "${IMG_PREFIX}/agent-worker:${VERSION}" -f services/agent-worker/Dockerfile .
docker build -t "${IMG_PREFIX}/agent-scheduler:${VERSION}" -f services/agent-scheduler/Dockerfile .
docker build -t "${IMG_PREFIX}/erpx-mock-api:${VERSION}" -f services/erpx-mock-api/Dockerfile .
docker build -t "${IMG_PREFIX}/ui:${VERSION}" -f services/ui/Dockerfile .

docker save \
  "${IMG_PREFIX}/agent-service:${VERSION}" \
  "${IMG_PREFIX}/agent-worker:${VERSION}" \
  "${IMG_PREFIX}/agent-scheduler:${VERSION}" \
  "${IMG_PREFIX}/erpx-mock-api:${VERSION}" \
  "${IMG_PREFIX}/ui:${VERSION}" \
  -o "${TAR}"

for host in "$@"; do
  scp "${TAR}" "${host}:${TAR}"
  ssh "${host}" "sudo k3s ctr images import ${TAR} && rm -f ${TAR}"
done

echo "Images loaded on: $*"

