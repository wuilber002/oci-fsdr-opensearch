#!/usr/bin/env bash
# Bootstrap a new OCI OpenSearch FSDR container deployment from this repository.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/runtime"
SECRET_ID=""
INSTALL_DOCKER=false
REPLACE_CONFIG=false

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install_container.sh --secret-id <vault-secret-ocid> [options]

Options:
  --install-docker  Install Docker Engine and the Compose plugin on a dnf-based VM.
  --replace-config  Replace the container configuration if it already exists.
  -h, --help        Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --secret-id) SECRET_ID="${2:-}"; shift 2 ;;
    --install-docker) INSTALL_DOCKER=true; shift ;;
    --replace-config) REPLACE_CONFIG=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo or as root." >&2
  exit 1
fi
if [[ ! "$SECRET_ID" =~ ^ocid1\.vaultsecret\.[A-Za-z0-9.-]+$ ]]; then
  echo "Provide a valid Vault Secret OCID with --secret-id." >&2
  exit 2
fi

install_docker() {
  if ! command -v dnf >/dev/null; then
    echo "Automatic Docker installation supports only dnf-based VMs. Install Docker Engine and its Compose plugin, then rerun." >&2
    exit 1
  fi
  dnf install -y dnf-plugins-core
  dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
  dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

if ! command -v docker >/dev/null; then
  if [[ "$INSTALL_DOCKER" == true ]]; then
    install_docker
  else
    echo "Docker Engine is required. Rerun with --install-docker or install Docker and the Compose plugin first." >&2
    exit 1
  fi
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required (docker compose version)." >&2
  exit 1
fi

install -d -m 0750 -o 10001 -g 10001 "$RUNTIME/config" "$RUNTIME/state" "$RUNTIME/log"
CONFIG="$RUNTIME/config/config.json"
if [[ -e "$CONFIG" && "$REPLACE_CONFIG" != true ]]; then
  echo "Configuration already exists: $CONFIG. Use --replace-config only after reviewing it." >&2
  exit 1
fi

umask 027
cat > "$CONFIG" <<EOF
{
  "secret_id": "$SECRET_ID",
  "inventory_enabled": true,
  "state_dir": "/var/lib/oci-opensearch-fsdr",
  "log_dir": "/var/log/oci-opensearch-fsdr",
  "ca_file": null,
  "request_timeout": 20,
  "wait_timeout": 1800,
  "poll_interval": 2,
  "use_private_ip": true,
  "leader_role": null,
  "follower_role": null,
  "inventory_interval_hours": 2,
  "max_inventory_age_hours": 4,
  "retention_days": 30,
  "service_timeout_seconds": 3600,
  "max_checkpoint_lag": 0,
  "enforce_rpo": false,
  "snapshot_repositories": null,
  "max_snapshot_age_hours": 24,
  "snapshot_required": false
}
EOF
chown 10001:10001 "$CONFIG"
chmod 0640 "$CONFIG"

cd "$ROOT"
docker compose -f compose.container.yaml build --pull
docker compose -f compose.container.yaml run --rm --no-deps fsdr-api bootstrap
# The token and private key remain mode 0600 for UID 10001.  Permit a local
# operator to traverse the known certificate path and use the public CA with
# curl, without making the state directory listable.
chmod 0711 "$RUNTIME/state" "$RUNTIME/state/api-secrets"
chmod 0644 "$RUNTIME/state/api-secrets/tls.crt"
docker compose -f compose.container.yaml up -d

echo "FSDR API is running at https://127.0.0.1:1604"
echo "Show its Bearer token locally:"
echo "  cd $ROOT && sudo docker compose -f compose.container.yaml exec fsdr-api python -m fsdr_opensearch.api token"
