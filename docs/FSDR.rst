FSDR workflow
=============

All operations are submitted to the local HTTPS API. They are serialized: only
one controller command is executed at a time. The web console shows the job
state and its captured output while it runs.

For an external FSDR orchestrator, prefer ``scripts/fsdr-job.sh`` over a raw
``POST`` request. The helper waits for the submitted job to finish and returns
``0`` only for ``succeeded``; all other terminal states and communication
errors return non-zero. For example::

    ./scripts/fsdr-job.sh validate-switchover
    ./scripts/fsdr-job.sh reprotect '{"reprotect_parallelism":2}'

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

Audited workflow gates
----------------------

The API persists a local workflow ledger alongside FSDR state. It refuses an
out-of-order submission with HTTP ``409`` and returns the unmet requirements.
This creates one auditable, resumable planned-switchover sequence:

#. ``inventory`` is accepted only in ``READY`` or ``PROTECTION_RESTORED``.
#. ``validate-switchover`` requires an inventory no older than one hour.
#. ``switchover`` additionally requires a successful validation for that exact
   inventory.
#. ``validate-post-switchover`` requires ``SERVICE_RESTORED`` and a successful
   switchover or failover as the latest successful workflow step.
#. ``reprotect`` requires ``SERVICE_RESTORED`` and a successful post-transition
   validation after that switchover or failover.

The dashboard also renders an explicit failover branch. It does not require a
fresh inventory or ``validate-switchover`` because the original primary may be
unavailable. It does require a persisted inventory, ``source_fenced=true`` and
``accept_data_loss=true``. When that inventory is older than one hour it also
requires ``use_follower_annotations=true`` so the reachable follower marker is
confirmed before promotion.

Inspect the same gates used by submission automation with ``GET /v1/workflow``.
The read-only dashboard renders the planned stages, requirement LEDs and status
explanations from this durable record. Explicit ``resume`` remains a separate
recovery decision and is not advanced automatically.

Pre-validation
--------------

``validate-switchover`` validates the green health of both clusters, CCR
aliases and roles, index replication markers, inventory freshness,
checkpoint/RPO gates and prerequisites for a planned switch. It is safe to call
repeatedly after a fresh inventory::

    api '{"command":"inventory"}'
    api '{"command":"validate-switchover"}'

Switchover
----------

The API ``switchover`` command executes by default. Its execution blocks writes on the
old primary, waits for final CCR checkpoints, stops followers on the target,
promotes the target by enabling writes, and records durable transition state::

    api '{"command":"switchover"}'

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
as its follower. The former-primary copies are deleted and recreated as part
of the reprotect command. ``reprotect_parallelism`` controls
the number of CCR bootstraps started together; use ``1`` for the least pressure
on network, I/O and shards::

    api '{"command":"reprotect","reprotect_parallelism":1}'

The controller waits for each batch to reach its CCR checkpoint. Large indices
may take a long time; the API job and web console remain available during the
wait. On success the phase becomes ``PROTECTION_RESTORED``.

Failover
--------

Failover is for loss of the former primary. It requires an explicit external
fencing attestation and acceptance of potential data loss. Do not use it for a
planned role change::

    api '{"command":"failover","source_fenced":true,"accept_data_loss":true}'

If the saved inventory is older than one hour, include
``use_follower_annotations=true``. The API workflow gate requires this explicit
confirmation before it accepts the failover request::

    api '{"command":"failover","source_fenced":true,"accept_data_loss":true,"use_follower_annotations":true}'

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
switchover or failover, submit the command with ``"resume":true`` only after
reviewing the state and audit log.
