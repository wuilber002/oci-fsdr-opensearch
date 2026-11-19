Container deployment, API and web console
=========================================

The repository is deployed as one Docker Compose service named ``fsdr-api``.
The image contains the FSDR controller and an HTTPS API; the host has no FSDR
Python runtime, systemd service, virtualenv or configuration directories.

Persistent runtime layout
-------------------------

The bootstrap script creates these paths relative to the repository checkout::

    runtime/config/config.json       Runtime configuration and Vault Secret OCID
    runtime/state/                   FSDR state, job records, API token and TLS material
    runtime/log/                     FSDR JSONL audit logs

``runtime/`` is deliberately ignored by Git. Back it up according to the DR
procedure; it contains durable FSDR state and an API credential.

Install from the repository
---------------------------

The VM must already have OCI Instance Principal configured and network access
to Vault/OCI APIs and both private OpenSearch endpoints. From a new VM::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /opt/oci-opensearch-fsdr
    cd /opt/oci-opensearch-fsdr
    sudo ./scripts/install_container.sh --install-docker --secret-id '<VAULT_SECRET_OCID>'

The ``--install-docker`` option installs Docker Engine and the Compose plugin
on a dnf-based Linux VM. Omit it when those components are already available.
The command builds the image, writes ``runtime/config/config.json``, creates a
random Bearer token and self-signed local TLS certificate, then starts the
service.

The container runs as UID/GID 10001 with a read-only root filesystem. Docker
host networking is required so OCI Instance Principal, private DNS, routing and
private OpenSearch endpoints are used exactly as from the executor VM. The API
binds only to ``127.0.0.1:1604``. Do not expose it directly to an untrusted
network.

Web console
-----------

Open this URL from the VM::

    https://127.0.0.1:1604/

For a workstation, create a tunnel and then open the same URL locally::

    ssh -L 1604:127.0.0.1:1604 opc@<VM_IP>

The dashboard is intentionally read-only and does not require the API Bearer
token. It is safe to use for a local operator display because Compose binds the
service only to ``127.0.0.1``; remote viewing must use an SSH tunnel. The screen
refreshes every five seconds and obtains current document counts and CCR status
without writing OpenSearch or FSDR state.

The dashboard keeps the console data structure—banner, paired cluster panels,
per-index comparison, execution console and time footer—using a web-oriented
colour palette.

HTTPS API
---------

The self-signed CA certificate is persisted at
``runtime/state/api-secrets/tls.crt``. Use it explicitly with curl::

    export FSDR_TOKEN='<token obtained locally>'
    curl --fail-with-body --cacert runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/healthz

Submit a job. Requests are asynchronous and exactly one job runs at a time::

    curl --fail-with-body --cacert runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" -H 'Content-Type: application/json' \
      -d '{"command":"validate-switchover"}' \
      https://127.0.0.1:1604/v1/jobs

Use the returned job ID to inspect its state and captured console output::

    curl --cacert runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/v1/jobs/<JOB_ID>
    curl --cacert runtime/state/api-secrets/tls.crt \
      -H "Authorization: Bearer $FSDR_TOKEN" \
      https://127.0.0.1:1604/v1/jobs/<JOB_ID>/output

``GET /v1/jobs`` and ``GET /v1/monitor`` require the Bearer token. The browser
uses separate read-only local endpoints under ``/dashboard/v1/``. ``GET
/healthz`` does not disclose state and is deliberately unauthenticated for
local health checks.

Supported commands and execution mode
-------------------------------------

The API accepts ``discover``, ``precheck``, ``inventory``, ``readiness``,
``validate-switchover``, ``switchover``, ``validate-post-switchover``,
``reprotect``, ``failover``, ``status`` and ``cleanup``. Request properties
match the controller flags, including ``execute``, ``resume``,
``allow_recreate`` and ``reprotect_parallelism``.

``switchover``, ``failover`` and ``reprotect`` default to dry-run behaviour.
They mutate only when the request body explicitly has ``"execute": true``.
A restarted container marks queued or in-progress jobs as ``interrupted`` and
does not retry them; the FSDR durable state is preserved for an explicit resume
decision.

Periodic inventory
------------------

The container itself replaces the old host timer. It reads
``inventory_enabled`` and ``inventory_interval_hours`` from
``runtime/config/config.json`` after every interval, and only submits an
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
