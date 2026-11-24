Future passive DR drill plan
============================

Status
------

This is a future implementation plan. It records the agreed design and does
not add a ``start-drill`` command or change current recovery behavior.

Objective
---------

Provide an auditable drill that demonstrates that the protected OpenSearch data
can be used at the DR site without provisioning another OCI OpenSearch service,
interrupting production traffic, changing the active primary, stopping CCR or
making follower indexes writable.

The first delivery is therefore a **passive read-only drill**, not a promotion
exercise. It must be described as such in the CLI, API and dashboard.

Why the follower cannot be promoted for this drill
--------------------------------------------------

The existing follower is the production standby. Its replicated indexes are
read-only while CCR is active. Making them writable requires stopping
replication, which changes the protected topology and turns the exercise into a
real switchover/failover-style recovery that later needs reprotection. That is
outside the safety boundary for a passive drill.

Scope of the first delivery
---------------------------

Introduce these API/CLI operations:

* ``validate-drill``: read-only precheck.
* ``start-drill``: opens a durable local drill session after precheck succeeds.
* ``validate-drill-environment``: executes configured read-only validation
  probes against the follower.
* ``stop-drill``: closes the session and writes its final audit result.

``start-drill`` must require a stable FSDR phase (``READY`` or
``PROTECTION_RESTORED``), a fresh inventory, healthy clusters, an active and
zero-lag CCR scope, matching FSDR markers and verified follower read-only
settings. The command captures the inventory, checkpoints, index/document
counts, cluster roles and timestamps as immutable drill evidence.

While a drill is active, planned switchover and reprotect must be rejected to
avoid overlapping control-plane actions. A real emergency failover remains
possible after the controller explicitly ends the passive drill and records
that interruption in the audit trail.

Read-only validation
--------------------

The drill environment checks must support:

* authenticated cluster health and index metadata reads;
* sampled search and aggregation queries;
* document-count comparison against captured follower evidence;
* mappings, aliases, templates and required pipelines;
* application connectivity using a dedicated read-only credential; and
* an expected write rejection, proving that the live follower remains fenced.

No test may change index mappings, settings, documents, aliases, templates,
pipelines, CCR configuration, DNS, load balancers or application routing.

Dashboard model
---------------

Render a separate **DR drill** section rather than a third recovery branch. It
must show the current drill state, drill identifier, started/finished time,
follower endpoint, inventory age, captured RPO/checkpoints and validation
result. The section must visibly state ``Read-only; no promotion``.

The recovery-path display continues to represent only actions that may restore
service: planned switchover and emergency failover.

Persistent evidence
-------------------

Store a durable drill manifest alongside the workflow ledger. Each execution
records the command/job identifiers, inventory timestamp, source and follower,
CCR checkpoints, protected-index scope, validation probes and their output
summary, operator-provided purpose, timestamps and final status. Keep the
manifest subject to the existing retention policy.

Optional second delivery: local snapshot restore
-------------------------------------------------

A later, opt-in ``restore-drill`` mode can run a temporary single-node
OpenSearch container on the executor host and restore a current snapshot from
a repository registered read-only. It avoids a new managed OCI OpenSearch
service but still consumes executor CPU, disk and object-storage transfer.

It can validate snapshot restoration, mappings, documents and read queries. It
does not prove managed-service capacity, multi-node behavior, traffic cutover,
write acceptance or production RTO. The passive drill remains the default.

Explicitly out of scope
-----------------------

The drill must not promote the follower, call ``stop_replication``, enable
writes on a protected follower, create a new OCI OpenSearch service by default,
change DNS/load balancers, route application traffic, or claim that it tested a
full failover. Full promotion testing remains a controlled switchover cycle.
