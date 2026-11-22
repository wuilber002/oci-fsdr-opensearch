OCI OpenSearch FSDR
===================

OCI OpenSearch FSDR is a container-first controller for planned switchover,
failover, reverse protection, inventory and evidence collection for an OCI
OpenSearch CCR pair. It uses OCI Instance Principal to read one Vault Secret
that identifies both clusters and contains their internal OpenSearch
credentials.

The supported deployment is the Docker Compose service in this repository. It
exposes a local-only HTTPS API on TCP 1604 and a read-only web console at the
same address. The web console shows the FSDR phase, primary/follower
direction, protected indices, current document counts, CCR status, API jobs
and captured command output.

Quick start
-----------

On a new executor VM, clone the repository and run the bootstrap script::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /opt/oci-opensearch-fsdr
    cd /opt/oci-opensearch-fsdr
    sudo ./scripts/install_container.sh --install-docker --secret-id '<VAULT_SECRET_OCID>'

Open ``https://127.0.0.1:1604`` locally on the VM, or through an SSH tunnel.
The installation creates only ``runtime/`` within the repository checkout. It
does not create a service account, host Python virtualenv, systemd unit, or
host-level FSDR configuration under ``/etc`` or ``/var``.

Documentation
-------------

* `Container deployment, API and web console <docs/CONTAINER.rst>`_
* `FSDR workflow and operational commands <docs/FSDR.rst>`_
* `Future multi-follower implementation plan <docs/MULTI_FOLLOWER_PLAN.rst>`_
* `Instance Principal IAM requirements <docs/IAM.rst>`_
* ``secret.example.json``: expected Vault Secret payload.

Scope and safety
----------------

Each protected index must declare ``mappings._meta.fsdr.replicate: true`` on
the leader and follower. The controller refuses DR operations when the marker
and discovered CCR scope differ. API requests for switchover, failover and
reprotect execute immediately when submitted.

The separate lab/demo setup remains isolated under ``lab/`` and is not part of
the container image, Docker Compose deployment, runtime volumes or API.

Validation
----------

Run the offline unit tests from a development environment::

    python3 -m unittest discover -s tests -v

The tests do not replace controlled validation of OCI IAM, Vault, private DNS,
network paths, TLS, CCR permissions, OpenSearch roles and the FSDR plan.
