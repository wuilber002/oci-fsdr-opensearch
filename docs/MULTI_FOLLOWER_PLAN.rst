Multi-follower implementation plan
==================================

Status: deferred for future implementation.

Objective
---------

Extend the controller from its current two-cluster active/passive model to a
single writable primary with one or more CCR followers. The dashboard must
always display the observed primary as the leftmost cluster, followed by every
follower on its right, independently of configured or discovery order.

Current boundary
----------------

The current configuration, lab, discovery, state and recovery commands model
the fixed aliases ``a`` and ``b``. Switchover promotes the only follower and
``reprotect`` rebuilds the former primary as its only follower. This is an
intentional two-site workflow; it cannot safely be generalized by merely
adding more dashboard cards.

Target model
------------

* Persist a collection of clusters with stable IDs, names, endpoints,
  credentials and observed roles.
* Persist, for every protected index, the leader and the replication health,
  checkpoints and UUIDs for each follower.
* Treat the topology as a directed fan-out: one primary can protect one or
  more followers. Bidirectional CCR remains unsupported.
* Select the promotion target explicitly for switchover and failover. Never
  infer it from configuration order.
* After promotion, rebuild the declared follower set deliberately, including
  the former primary only when it is available and eligible to rejoin.

Implementation phases
---------------------

1. **Read-only multi-cluster discovery and dashboard**

   Replace fixed ``a``/``b`` monitor records with a collection keyed by stable
   cluster ID. Render the observed primary first and a responsive follower
   area to its right. Preserve per-follower pagination, CCR status, lag and
   FSDR marker warnings. This phase does not change recovery behavior.

2. **Configuration, inventory and evidence schema**

   Replace pair-specific secret/configuration fields with ``clusters[]`` and
   explicit remote aliases. Version the persisted state so a running
   two-cluster installation can be migrated safely. Inventory must record the
   primary-to-each-follower relation for every FSDR-marked index and report
   partial follower failure without hiding healthy replicas.

3. **Multi-follower validation and command gates**

   Add target selection to planned switchover and failover. Validate a chosen
   target separately from the set of followers that must remain protected.
   Define a policy for unhealthy or unreachable non-target followers: reject,
   explicitly exclude with audit evidence, or require an operator override.

4. **Promotion and reprotection**

   Generalize write fencing, promotion and CCR stop/start operations for the
   selected target. Reprotect must reconstruct the intended fan-out from the
   new primary, one follower at a time, with durable per-follower progress and
   safe resume semantics.

5. **Lab, tests and documentation**

   Extend lab provisioning, demo data scripts, cleanup, resilience tests and
   operational documentation to cover three or more clusters, partial
   follower loss, target selection, rejoin and interrupted reprotection.

Decisions required before phase 3
---------------------------------

* Which follower is eligible for promotion: operator-selected only, or may a
  policy choose the follower with the best checkpoint/RPO?
* Does a failed non-target follower block switchover, or can it be excluded
  with an explicit audited acknowledgement?
* After promotion, must every surviving follower be rebuilt before the state
  may become ``PROTECTION_RESTORED``?
* How are application routing and regional preference represented outside the
  controller?

Acceptance criteria
-------------------

The implementation is complete only when discovery order cannot affect role
selection or screen order; the primary is always leftmost; every follower has
independent health and lag evidence; every destructive action names its target
and affected followers; and an interrupted multi-follower reprotect can be
audited and resumed without creating divergent CCR directions.
