#!/usr/bin/env bash
# Submit one authenticated controller job to the local container API.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
command_name=${1:-}

if [[ -z "$command_name" || $# -ne 1 ]]; then
  echo "Usage: $0 <command>" >&2
  echo "Example: $0 inventory" >&2
  exit 2
fi
if [[ ! "$command_name" =~ ^[a-z][a-z-]*$ ]]; then
  echo "Command must contain lowercase letters and hyphens only." >&2
  exit 2
fi

token=$(sudo cat "$root/runtime/state/api-secrets/api-token")
certificate="$root/runtime/state/api-secrets/tls.crt"

if [[ ! -r "$certificate" ]]; then
  echo "Cannot read $certificate. Rerun scripts/install_container.sh to repair runtime permissions." >&2
  exit 1
fi

curl --fail-with-body --cacert "$certificate" \
  -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' \
  -d "{\"command\":\"${command_name}\"}" \
  https://127.0.0.1:1604/v1/jobs
