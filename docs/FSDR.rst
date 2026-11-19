FSDR workflow
=============

All operations are submitted to the local HTTPS API. They are serialized: only
one controller command is executed at a time. The web console shows the job
state and its captured output while it runs.

Request format
--------------

Use ``POST /v1/jobs`` with a Bearer token. Example helper variables::

    export FSDR_TOKEN='<local token>'
    API='https://127.0.0.1:1604'
    CA='runtime/state/api-secrets/tls.crt'
    api() {
      curl --fail-with-body --cacert "$CA" \
        -H "Authorization: Bearer $FSDR_TOKEN" -H 'Content-Type: application/json' \
        -d "$1" "$API/v1/jobs"
    }

Pre-validation
--------------

``validate-switchover`` refreshes the inventory, validates the green health of
both clusters, CCR aliases and roles, index replication markers, inventory
freshness, checkpoint/RPO gates and prerequisites for a planned switch. It is
safe to call repeatedly::

    api '{"command":"validate-switchover"}'

Switchover
----------

``switchover`` is dry-run by default. Its real execution blocks writes on the
old primary, waits for final CCR checkpoints, stops followers on the target,
promotes the target by enabling writes, and records durable transition state::

    api '{"command":"switchover","execute":true}'

The phase becomes ``SERVICE_RESTORED``. Application traffic/routing is an
external FSDR step and must only point to the promoted cluster after its own
validation.

Post-validation
---------------

``validate-post-switchover`` requires ``SERVICE_RESTORED``. It validates the
promoted primary, expected write blocks and protected-index document counts
before reverse protection starts::

    api '{"command":"validate-post-switchover"}'

Reverse protection
------------------

``reprotect`` preserves the promoted primary and rebuilds the previous primary
as its follower. The former-primary copies are deleted and recreated, so the
request requires explicit authorization. ``reprotect_parallelism`` controls
the number of CCR bootstraps started together; use ``1`` for the least pressure
on network, I/O and shards::

    api '{"command":"reprotect","execute":true,"allow_recreate":true,"reprotect_parallelism":1}'

The controller waits for each batch to reach its CCR checkpoint. Large indices
may take a long time; the API job and web console remain available during the
wait. On success the phase becomes ``PROTECTION_RESTORED``.

Failover
--------

Failover is for loss of the former primary. It requires an explicit external
fencing attestation and acceptance of potential data loss. Do not use it for a
planned role change::

    api '{"command":"failover","execute":true,"source_fenced":true,"accept_data_loss":true}'

If the source cannot be reached and the inventory is stale, the additional
``allow_stale_inventory`` and ``use_follower_annotations`` flags can be used
only after an operator has confirmed the follower annotations and risk.

Inventory and marker requirements
---------------------------------

The inventory remains authoritative for CCR direction, index UUIDs and
checkpoints. Every protected leader and follower index must also have this
mapping metadata::

    PUT <index>/_mapping
    {
      "_meta": {
        "fsdr": {
          "replicate": true
        }
      }
    }

The controller rejects operations if marked indices and the discovered CCR
scope do not match exactly. This hybrid rule prevents an incomplete inventory
or an unmarked replicated index from silently entering a DR operation.

Status, history and resume
--------------------------

Use ``{"command":"status"}`` to retrieve durable state, or inspect a job by
``GET /v1/jobs/<id>`` and its captured output by
``GET /v1/jobs/<id>/output``. A restarted API marks a running job as
``interrupted`` and never repeats it automatically. For an interrupted
switchover or failover, submit the same command with ``"execute":true`` and
``"resume":true`` only after reviewing the state and audit log.
