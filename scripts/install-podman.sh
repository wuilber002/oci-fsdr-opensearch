#!/usr/bin/env bash
# Install or update OCI OpenSearch FSDR without retaining the source checkout.
set -euo pipefail

SOURCE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FSDR_HOME=${FSDR_HOME:-/opt/oci-fsdr-opensearch}
SECRET_ID=""
REPLACE_CONFIG=false
SKIP_DEPENDENCIES=false
UPDATE=false

usage() {
  cat <<'USAGE'
Usage:
  sudo ./scripts/install-podman.sh --secret-id <vault-secret-ocid> [options]
  sudo ./scripts/install-podman.sh --update [options]

This copies the deployment files to /opt/oci-fsdr-opensearch, builds a local
Podman image and starts the controller. The repository checkout is not used at
runtime and may be removed after a successful installation or update.

Options:
  --secret-id <ocid>    Required for the first installation. Preserved on update.
  --replace-config      Replace the persistent runtime configuration.
  --skip-dependencies   Do not install Podman and host utilities with dnf.
  --update              Rebuild/redeploy while preserving runtime configuration and state.
  -h, --help            Show this help.
USAGE
}

while (($#)); do
  case "$1" in
    --secret-id) SECRET_ID=${2:-}; shift 2 ;;
    --replace-config) REPLACE_CONFIG=true; shift ;;
    --skip-dependencies) SKIP_DEPENDENCIES=true; shift ;;
    --update) UPDATE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run this installer with sudo or as root." >&2
  exit 1
fi
if [[ "$FSDR_HOME" != "/opt/oci-fsdr-opensearch" ]]; then
  echo "FSDR_HOME must be /opt/oci-fsdr-opensearch because the Podman deployment uses that persistent host path." >&2
  exit 2
fi
if [[ ! -f "$SOURCE_ROOT/Dockerfile" || ! -f "$SOURCE_ROOT/scripts/fsdr-job.sh" ]]; then
  echo "Run this script from a complete oci-opensearch-fsdr repository checkout." >&2
  exit 1
fi
if [[ -n "$SECRET_ID" && ! "$SECRET_ID" =~ ^ocid1\.vaultsecret\.[A-Za-z0-9.-]+$ ]]; then
  echo "Provide a valid Vault Secret OCID with --secret-id." >&2
  exit 2
fi

install_dependencies() {
  if ! command -v dnf >/dev/null 2>&1; then
    echo "Automatic dependency installation supports dnf-based hosts only. Install podman, python3, curl, openssl and ca-certificates, then rerun with --skip-dependencies." >&2
    exit 1
  fi
  dnf install -y podman python3 curl openssl ca-certificates
  systemctl enable --now podman-restart.service 2>/dev/null || true
}

if [[ "$SKIP_DEPENDENCIES" != true ]]; then
  install_dependencies
fi
for command in podman python3 curl openssl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required host command is missing: $command" >&2
    exit 1
  fi
done
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "Python 3.8 or newer is required on the host for the job helper." >&2
  exit 1
fi

install -d -m 0750 "$FSDR_HOME/bin" "$FSDR_HOME/runtime/config" "$FSDR_HOME/runtime/state" "$FSDR_HOME/runtime/log"
install -m 0755 "$SOURCE_ROOT/scripts/fsdr-job.sh" "$FSDR_HOME/bin/fsdr-job.sh"
install -m 0755 "$SOURCE_ROOT/scripts/fsdr-build-image.sh" "$FSDR_HOME/bin/fsdr-build-image.sh"
chown -R 10001:10001 "$FSDR_HOME/runtime"
# Allow the local operator to execute the helper and read the public CA
# certificate without granting directory listing or access to secrets/config.
chmod 0711 "$FSDR_HOME" "$FSDR_HOME/bin" "$FSDR_HOME/runtime" "$FSDR_HOME/runtime/state"
chmod 0750 "$FSDR_HOME/runtime/config" "$FSDR_HOME/runtime/log"

config="$FSDR_HOME/runtime/config/config.json"
if [[ ! -e "$config" || "$REPLACE_CONFIG" == true ]]; then
  if [[ -z "$SECRET_ID" ]]; then
    echo "--secret-id is required because $config does not exist." >&2
    exit 2
  fi
  umask 027
  cat > "$config" <<CONFIG
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
CONFIG
  chown 10001:10001 "$config"
  chmod 0640 "$config"
elif [[ -n "$SECRET_ID" ]]; then
  echo "Keeping existing configuration at $config (use --replace-config to change it)." >&2
fi

"$FSDR_HOME/bin/fsdr-build-image.sh" "$SOURCE_ROOT"
container_options=(
  --network host
  --user 10001:10001
  --read-only
  --tmpfs /tmp:mode=0700
  --env FSDR_API_HOST=127.0.0.1
  --env FSDR_API_PORT=1604
  --env FSDR_CONFIG=/etc/oci-opensearch-fsdr/config.json
  --env FSDR_API_JOB_DIR=/var/lib/oci-opensearch-fsdr/api-jobs
  --volume "$FSDR_HOME/runtime/config:/etc/oci-opensearch-fsdr:ro,Z"
  --volume "$FSDR_HOME/runtime/state:/var/lib/oci-opensearch-fsdr:rw,Z"
  --volume "$FSDR_HOME/runtime/log:/var/log/oci-opensearch-fsdr:rw,Z"
)
image=localhost/oci-opensearch-fsdr:local
podman run --rm "${container_options[@]}" "$image" bootstrap
# The API token/private key stay private to UID 10001. The local helper only
# needs traversal and the public CA certificate.
chmod 0711 "$FSDR_HOME/runtime/state" "$FSDR_HOME/runtime/state/api-secrets"
chmod 0644 "$FSDR_HOME/runtime/state/api-secrets/tls.crt"
podman rm --force oci-opensearch-fsdr >/dev/null 2>&1 || true
podman run --detach --name oci-opensearch-fsdr --restart=always \
  "${container_options[@]}" "$image"

if [[ "$UPDATE" == true ]]; then
  action="updated"
else
  action="installed"
fi
echo "FSDR controller $action at $FSDR_HOME."
echo "The source checkout can now be removed."
echo "Run jobs with: $FSDR_HOME/bin/fsdr-job.sh inventory"
echo "Dashboard: https://127.0.0.1:1604"
