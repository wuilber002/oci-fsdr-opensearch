#!/usr/bin/env bash
# Build the controller image from a temporary repository checkout.
set -euo pipefail

source_root=${1:-}
image=${FSDR_IMAGE:-localhost/oci-opensearch-fsdr:local}

if [[ -z "$source_root" || ! -f "$source_root/Dockerfile" || ! -d "$source_root/fsdr_opensearch" ]]; then
  echo "Usage: $0 <repository-checkout>" >&2
  echo "The checkout is used only as a build context and may be removed after this command." >&2
  exit 2
fi
if ! command -v podman >/dev/null 2>&1; then
  echo "Podman is required. Run install-podman.sh first." >&2
  exit 1
fi

podman build --pull=always --tag "$image" --file "$source_root/Dockerfile" "$source_root"
