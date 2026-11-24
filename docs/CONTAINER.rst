Container deployment, API and web console
=========================================

The deployment is one Podman Compose service named ``fsdr-api``. The image
contains the FSDR controller and its Python 3.9 runtime. The host keeps only
Podman, Python for the command-line helper, and the persistent operational
files under ``/opt/oci-opensearch-fsdr``. It does not use the Git checkout at
runtime.

Persistent runtime layout
-------------------------

The installer creates these durable paths on the host::

    /opt/oci-opensearch-fsdr/bin/fsdr-job.sh       Local authenticated job helper
    /opt/oci-opensearch-fsdr/compose.yaml          Podman service definition
    /opt/oci-opensearch-fsdr/runtime/config/       Runtime configuration and Vault Secret OCID
    /opt/oci-opensearch-fsdr/runtime/state/        FSDR state, jobs, API token and TLS material
    /opt/oci-opensearch-fsdr/runtime/log/          FSDR JSONL audit logs

Back up ``/opt/oci-opensearch-fsdr/runtime`` according to the DR procedure;
it contains durable FSDR state and an API credential. Do not copy it between
environments.

Install from the repository
---------------------------

The VM must already have OCI Instance Principal configured and network access
to Vault/OCI APIs and both private OpenSearch endpoints. Clone the repository
anywhere convenient; it is build input only and can be removed after a
successful installation::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /tmp/oci-opensearch-fsdr
    cd /tmp/oci-opensearch-fsdr
    sudo ./scripts/install-podman.sh --secret-id '<VAULT_SECRET_OCID>'
    rm -rf /tmp/oci-opensearch-fsdr

The installer installs Podman, Podman Compose, Python, curl, OpenSSL and CA
certificates on a dnf-based Linux VM. Use ``--skip-dependencies`` only when
those host commands are already installed. It builds the local image, writes
``/opt/oci-opensearch-fsdr/runtime/config/config.json``, creates a random
Bearer token and self-signed local TLS certificate, then starts the service.

For a new VM, run this installer instead of copying a ``runtime`` directory
from another environment. Each deployment creates its own API token, TLS key,
discovery cache and job history. The OCI Secret OCID is the only bootstrap
input; OCI aliases, IAM permissions and network routes must already exist.

The container runs as UID/GID 10001 with a read-only root filesystem. Podman
host networking is required so OCI Instance Principal, private DNS, routing and
private OpenSearch endpoints are used exactly as from the executor VM. The API
binds only to ``127.0.0.1:1604``. Do not expose it directly to an untrusted
network.

Update an existing installation
-------------------------------

To update the controller, clone the desired revision into a temporary path and
run the same installer with ``--update``. It replaces the image, compose file
and helper scripts, then recreates the container. It deliberately preserves
``runtime/config``, ``runtime/state`` and ``runtime/log``::

    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /tmp/oci-opensearch-fsdr-update
    cd /tmp/oci-opensearch-fsdr-update
    git checkout <release-or-commit>
    sudo ./scripts/install-podman.sh --update
    rm -rf /tmp/oci-opensearch-fsdr-update

Use ``--secret-id`` only on the first installation. Supplying it during an
update keeps the existing configuration; use ``--replace-config`` together
with ``--secret-id`` only when intentionally replacing the persistent policy.
Take a backup of the runtime directory before an update that changes workflow
or state compatibility.

Configuration and durable state
-------------------------------

``/opt/oci-opensearch-fsdr/runtime/config/config.json`` is the persistent
bootstrap configuration. It
contains the Vault Secret OCID and operational policy such as timeouts, RPO
gates and private-IP use; it never contains OpenSearch passwords. The container
reads it as UID/GID 10001 and mounts it read-only. Discovery cache, inventory,
job records, API token and TLS material are durable runtime state under
``/opt/oci-opensearch-fsdr/runtime/state``. Keeping the two stores separate
prevents an operational job
from silently changing its own DR policy.

Web console
-----------

Open this URL from the VM::

    https://127.0.0.1:1604/

For a workstation, create a tunnel and then open the same URL locally::

    ssh -L 1604:127.0.0.1:1604 opc@<VM_IP>

The dashboard is intentionally read-only and does not require the API Bearer
token. It is safe to use for a local operator display because Compose binds the
service only to ``127.0.0.1``; remote viewing must use an SSH tunnel. Job,
state, workflow-gate changes, job history and paired-cluster comparison arrive
through one Server-Sent Events stream. The stream sends a full snapshot on
connection and whenever durable FSDR state or job lifecycle changes; the backend
also refreshes read-only document counts and CCR status on a bounded interval.
The browser does not poll dashboard endpoints or make follow-up data requests.
Job submission remains an authenticated HTTP ``POST`` because SSE is
server-to-browser only.

The dashboard keeps the console data structure—banner, paired cluster panels,
per-index comparison, execution console and time footer—using a web-oriented
colour palette.

HTTPS API
---------

The self-signed CA certificate is persisted at
``/opt/oci-opensearch-fsdr/runtime/state/api-secrets/tls.crt``. Use it explicitly with curl::

    export FSDR_TOKEN='<token obtained locally>'
    curl --fail-with-body --cacert /opt/oci-opensearch-fsdr/runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/healthz

The installer keeps the token and private key private to container UID 10001.
It grants local users traverse-only access to the certificate path and read
access to ``tls.crt``; no API credential becomes readable as a result.

Submit a job. Requests are asynchronous and exactly one job runs at a time::

    curl --fail-with-body --cacert /opt/oci-opensearch-fsdr/runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" -H 'Content-Type: application/json' \
      -d '{"command":"validate-switchover"}' \
      https://127.0.0.1:1604/v1/jobs

For automation, use the repository helper. It reads the local token safely,
uses the persisted CA certificate, submits the job, and polls until the job
reaches a terminal state::

    /opt/oci-opensearch-fsdr/bin/fsdr-job.sh inventory
    /opt/oci-opensearch-fsdr/bin/fsdr-job.sh reprotect '{"reprotect_parallelism":2}'

The helper writes its progress to standard error and prints the final job
record plus captured controller output to standard output. It exits with
``0`` only when the API reports ``succeeded``. For a failed job, it preserves
the controller exit code when it is between ``1`` and ``125``; interrupted,
invalid, rejected, and transport failures return a non-zero exit code. When
the API rejects a request because its workflow prerequisites are not met, the
helper prints each unmet prerequisite and its explanation. The API also stores
that rejection as a ``rejected`` audit job, so its reason is available in the
dashboard history and the selected job log. This makes the helper suitable as
an external FSDR workflow step.

To inspect an asynchronously submitted job manually, use its returned job ID
and captured console output::

    curl --cacert /opt/oci-opensearch-fsdr/runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/v1/jobs/<JOB_ID>
    curl --cacert /opt/oci-opensearch-fsdr/runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/v1/jobs/<JOB_ID>/output

``GET /v1/jobs``, ``GET /v1/monitor`` and ``GET /v1/workflow`` require the
Bearer token. ``/v1/workflow`` returns the durable planned-switchover gates and
audit evidence used when accepting each workflow job. The browser uses separate
read-only local endpoints under ``/dashboard/v1/``. ``GET /healthz`` does not
disclose state and is deliberately unauthenticated for local health checks.

Supported commands and execution mode
-------------------------------------

The API accepts ``discover``, ``precheck``, ``inventory``, ``readiness``,
``validate-switchover``, ``switchover``, ``validate-post-switchover``,
``reprotect``, ``failover``, ``status`` and ``cleanup``. Request properties
match the controller flags, including ``resume``,
``reprotect_parallelism``.

``switchover``, ``failover`` and ``reprotect`` execute immediately because
the API is the OCI FSDR automation interface. The API rejects the obsolete
``execute`` and ``dry_run`` fields.
A restarted container marks queued or in-progress jobs as ``interrupted`` and
does not retry them; the FSDR durable state is preserved for an explicit resume
decision.

Periodic inventory
------------------

The container itself replaces the old host timer. It reads
``inventory_enabled`` and ``inventory_interval_hours`` from
``/opt/oci-opensearch-fsdr/runtime/config/config.json`` after every interval,
and only submits an
inventory job when the API worker is idle. Scheduled inventory is skipped
while a DR transition owns the controller.

On a new deployment with a configured ``secret_id`` and no durable state
file, the container immediately queues one ``inventory`` job at
startup. That job performs OCI discovery and then builds the CCR inventory;
its progress and outcome are visible in the dashboard. It is not repeated on
ordinary restarts after discovery/state has been persisted.

Private endpoint connectivity
-----------------------------

``use_private_ip`` defaults to ``true``. The controller obtains each
OpenSearch private IP from OCI and connects to that address while retaining
the OCI FQDN in the HTTPS URL for TLS SNI and certificate validation. This
avoids a dependency on cross-region private DNS resolution. The VCN route and
network rules must still allow the VM to reach TCP/9200 on both private IPs.
