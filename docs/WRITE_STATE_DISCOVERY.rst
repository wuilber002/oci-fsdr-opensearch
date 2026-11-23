Write-state discovery
=====================

The dashboard reports the effective document-write state observable by the
FSDR controller for every protected index. It never writes a probe document:
a probe could change data, execute ingest pipelines, or require permissions
that differ from the application.

The result describes controller-observable document writes. Application
writes can still fail because of application privileges, mappings, pipelines,
disk capacity, or request content.

Decision order
--------------

For each cluster the observer reads index settings, the effective cluster
settings, and index-level cluster health in batched read-only requests. It
then reads the CCR status of each protected index. The first matching rule
wins:

#. ``read_only`` when ``cluster.blocks.read_only`` or
   ``cluster.blocks.read_only_allow_delete`` is enabled.
#. ``read_only`` when the index has ``index.blocks.write``,
   ``index.blocks.read_only``, ``index.blocks.read_only_allow_delete``, or
   ``index.blocks.search_only`` enabled.
#. ``unavailable`` when the index is red or has no active primary shard.
#. ``read_only`` for a CCR follower with status ``SYNCING``, ``BOOTSTRAPPING``,
   or ``PAUSED``. A follower becomes a normal writable index only after
   replication is stopped.
#. ``read_write`` when none of the preceding restrictions applies.
#. ``unknown`` when the observer cannot collect the required evidence.

``index.blocks.metadata`` is deliberately excluded. It blocks metadata
changes, but does not itself block normal document indexing.

Dashboard presentation
----------------------

``read_write`` displays an open lock. ``read_only`` displays a closed lock.
Both retain the cluster-role color: gold for primary and silver for follower.
``unavailable`` and ``unknown`` do not claim a writable or read-only result;
their tooltip explains the missing or failed evidence.
