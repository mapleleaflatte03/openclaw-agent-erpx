#!/bin/sh
set -eu

NS="${NS:-openclaw-agent}"

OCR="${OCR:-}"
EXPORT="${EXPORT:-}"
IO="${IO:-}"
STANDBY="${STANDBY:-}"

usage() {
  echo "Usage:"
  echo "  OCR=2 EXPORT=1 IO=6 $0"
  echo "  STANDBY=1 $0   (scale standby worker)"
}

if [ -z "${OCR}${EXPORT}${IO}${STANDBY}" ]; then
  usage
  exit 1
fi

if [ -n "${OCR}" ]; then
  kubectl -n "${NS}" scale deploy/agent-worker-ocr --replicas="${OCR}"
fi
if [ -n "${EXPORT}" ]; then
  kubectl -n "${NS}" scale deploy/agent-worker-export --replicas="${EXPORT}"
fi
if [ -n "${IO}" ]; then
  kubectl -n "${NS}" scale deploy/agent-worker-io --replicas="${IO}"
fi
if [ -n "${STANDBY}" ]; then
  kubectl -n "${NS}" scale deploy/agent-worker-standby --replicas="${STANDBY}"
fi

kubectl -n "${NS}" get deploy | grep agent-worker || true

