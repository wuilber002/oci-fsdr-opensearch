#!/usr/bin/env bash
# Submit one authenticated controller job and wait for its terminal result.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
command_name=${1:-}
parameters=${2:-'{}'}
detail_option=${3:-}
poll_interval=${FSDR_JOB_POLL_INTERVAL:-2}

if [[ -z "$command_name" || $# -gt 3 ]]; then
  echo "Usage: $0 <command> [json-parameters]" >&2
  echo "       $0 log <JOB_ID> [--debug]" >&2
  echo "Example: $0 inventory" >&2
  echo "Example: $0 reprotect '{\"reprotect_parallelism\":2}'" >&2
  exit 2
fi
if [[ "$command_name" != "log" && "$command_name" != "result" && $# -gt 2 ]]; then
  echo "A controller command accepts at most one JSON parameter object." >&2
  exit 2
fi
if [[ ! "$command_name" =~ ^[a-z][a-z-]*$ ]]; then
  echo "Command must contain lowercase letters and hyphens only." >&2
  exit 2
fi

token=$(sudo cat "$root/runtime/state/api-secrets/api-token")
certificate="$root/runtime/state/api-secrets/tls.crt"

if [[ ! -r "$certificate" ]]; then
  echo "Cannot read $certificate. Rerun scripts/install-podman.sh to repair runtime permissions." >&2
  exit 1
fi

if [[ "$command_name" != "log" && "$command_name" != "result" ]]; then
payload=$(python3 - "$command_name" "$parameters" <<'PY'
import json
import sys

command, raw = sys.argv[1:]
try:
    parameters = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit("Invalid JSON parameters: {}".format(exc.msg))
if not isinstance(parameters, dict):
    raise SystemExit("JSON parameters must be an object")
if "command" in parameters:
    raise SystemExit("JSON parameters must not contain command")
parameters["command"] = command
print(json.dumps(parameters, separators=(",", ":")))
PY
)
fi

api() {
  curl --fail-with-body --silent --show-error --cacert "$certificate" \
    -H "Authorization: Bearer $token" "$@"
}

if [[ "$command_name" == "log" || "$command_name" == "result" ]]; then
  if [[ $# -lt 2 || $# -gt 3 || ! "$parameters" =~ ^[a-f0-9]{32}$ ]]; then
    echo "Usage: $0 $command_name <JOB_ID>" >&2
    exit 2
  fi
  if [[ "$command_name" == "result" && -n "$detail_option" ]]; then
    echo "The result command does not accept log-level options." >&2
    exit 2
  fi
  endpoint="output"
  suffix=""
  if [[ "$command_name" == "log" ]]; then
    if [[ -n "$detail_option" && "$detail_option" != "--debug" ]]; then
      echo "Usage: $0 log <JOB_ID> [--debug]" >&2
      exit 2
    fi
    [[ "$detail_option" == "--debug" ]] && suffix="?levels=DEBUG%2CINFO%2CWARN%2CERROR"
  else
    endpoint="result"
  fi
  api "https://127.0.0.1:1604/v1/jobs/${parameters}/${endpoint}${suffix}"
  exit 0
fi

response_file=$(mktemp)
trap 'rm -f "$response_file"' EXIT
http_status=$(curl --silent --show-error --output "$response_file" --write-out '%{http_code}' \
  --cacert "$certificate" -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' -d "$payload" https://127.0.0.1:1604/v1/jobs) || {
  echo "Unable to submit FSDR job: the local API could not be reached." >&2
  exit 1
}

if [[ "$http_status" != "202" ]]; then
  # A workflow rejection is persisted as a job so the dashboard/API can show
  # its complete explanation. Keep this helper automation-friendly: emit only
  # that ID and return a failing status.
  if [[ "$http_status" == "409" ]]; then
    python3 - "$response_file" <<'PY'
import json
import sys

try:
    detail = json.load(open(sys.argv[1], encoding="utf-8")).get("detail", {})
except (OSError, ValueError):
    detail = {}
if isinstance(detail, dict) and detail.get("code") == "workflow_prerequisites_unmet":
    job_id = detail.get("job_id")
    if isinstance(job_id, str):
        print(job_id)
PY
    exit 1
  fi
  python3 - "$http_status" "$response_file" <<'PY' >&2
import json
import sys

http_status, filename = sys.argv[1:]
try:
    payload = json.load(open(filename, encoding="utf-8"))
except (OSError, ValueError):
    payload = {}
detail = payload.get("detail", payload)
if not isinstance(detail, dict):
    print("FSDR job request failed (HTTP {}): {}".format(http_status, detail or "no response body"))
    raise SystemExit()

message = detail.get("message") or detail.get("code") or "request rejected"
print("FSDR job request failed (HTTP {}): {}".format(http_status, message))
PY
  exit 1
fi

submitted=$(cat "$response_file")
job_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$submitted")
# The submitted ID is the sole normal output, suitable for orchestration.
printf '%s\n' "$job_id"

while true; do
  job=$(api "https://127.0.0.1:1604/v1/jobs/${job_id}")
  read -r status exit_code < <(python3 -c '
import json, sys
job = json.load(sys.stdin)
print(job.get("status", "unknown"), job.get("exit_code") if job.get("exit_code") is not None else "")
' <<<"$job")
  case "$status" in
    queued|running)
      sleep "$poll_interval"
      ;;
    succeeded|failed|interrupted|rejected)
      break
      ;;
    *)
      printf 'FSDR job %s returned an unknown status: %s\n' "$job_id" "$status" >&2
      exit 1
      ;;
  esac
done

if [[ "$status" == succeeded ]]; then
  exit 0
fi
if [[ "$exit_code" =~ ^[1-9][0-9]*$ ]] && (( exit_code <= 125 )); then
  exit "$exit_code"
fi
exit 1
