#!/usr/bin/env bash
# Submit one authenticated controller job and wait for its terminal result.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
command_name=${1:-}
parameters=${2:-'{}'}
poll_interval=${FSDR_JOB_POLL_INTERVAL:-2}

if [[ -z "$command_name" || $# -gt 2 ]]; then
  echo "Usage: $0 <command> [json-parameters]" >&2
  echo "Example: $0 inventory" >&2
  echo "Example: $0 reprotect '{\"reprotect_parallelism\":2}'" >&2
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

api() {
  curl --fail-with-body --silent --show-error --cacert "$certificate" \
    -H "Authorization: Bearer $token" "$@"
}

response_file=$(mktemp)
trap 'rm -f "$response_file"' EXIT
http_status=$(curl --silent --show-error --output "$response_file" --write-out '%{http_code}' \
  --cacert "$certificate" -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' -d "$payload" https://127.0.0.1:1604/v1/jobs) || {
  echo "Unable to submit FSDR job: the local API could not be reached." >&2
  exit 1
}

if [[ "$http_status" != "202" ]]; then
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

if http_status == "409" and detail.get("code") == "workflow_prerequisites_unmet":
    print("FSDR job rejected before execution: {}.".format(detail.get("command", "requested command")))
    print("Reason: workflow prerequisites are not satisfied.")
    job_id = detail.get("job_id")
    if job_id:
        print("Recorded in dashboard history as rejected job: {}.".format(job_id))
    inventory = detail.get("inventory") or {}
    age = inventory.get("age_seconds")
    maximum = inventory.get("max_age_seconds")
    if isinstance(age, (int, float)) and isinstance(maximum, (int, float)):
        def duration(seconds):
            minutes = max(0, int(seconds) // 60)
            hours, minutes = divmod(minutes, 60)
            return (f"{hours}h {minutes}m" if hours else f"{minutes}m")
        print("Inventory age: {} (limit {}; {}).".format(
            duration(age), duration(maximum),
            "valid" if inventory.get("fresh") else "expired"))
    elif inventory.get("detail"):
        print("Inventory: {}".format(inventory["detail"]))
    unmet = [item for item in detail.get("requirements", []) if not item.get("met")]
    if unmet:
        print("Unmet requirements:")
        for item in unmet:
            print("- {}".format(item.get("detail") or item.get("key") or "Requirement not satisfied"))
            if item.get("explanation"):
                print("  {}".format(item["explanation"]))
else:
    message = detail.get("message") or detail.get("code") or "request rejected"
    print("FSDR job request failed (HTTP {}): {}".format(http_status, message))
PY
  exit 1
fi

submitted=$(cat "$response_file")
job_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$submitted")
printf 'FSDR job submitted: %s\n' "$job_id" >&2

last_status=''
while true; do
  job=$(api "https://127.0.0.1:1604/v1/jobs/${job_id}")
  read -r status exit_code < <(python3 -c '
import json, sys
job = json.load(sys.stdin)
print(job.get("status", "unknown"), job.get("exit_code") if job.get("exit_code") is not None else "")
' <<<"$job")
  if [[ "$status" != "$last_status" ]]; then
    printf 'FSDR job %s: %s\n' "$job_id" "$status" >&2
    last_status=$status
  fi
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

printf 'Controller log for %s:\n' "$job_id"
api "https://127.0.0.1:1604/v1/jobs/${job_id}/output" || true
printf '\nStructured result JSON: https://127.0.0.1:1604/v1/jobs/%s/result\n' "$job_id" >&2
printf 'Local controller result: %s/runtime/state/api-jobs/%s.result.json\n' "$root" "$job_id" >&2

if [[ "$status" == succeeded ]]; then
  exit 0
fi
if [[ "$exit_code" =~ ^[1-9][0-9]*$ ]] && (( exit_code <= 125 )); then
  exit "$exit_code"
fi
exit 1
