OCI OpenSearch FSDR
===================

.. image:: fsdr_opensearch/static/fsdr-logo.png
   :alt: OCI OpenSearch FSDR shield logo
   :width: 128px
   :align: center

.. |tests| image:: https://github.com/wuilber002/oci-fsdr-opensearch/actions/workflows/tests.yml/badge.svg?branch=main
   :target: https://github.com/wuilber002/oci-fsdr-opensearch/actions/workflows/tests.yml
   :alt: Automated tests

.. |python| image:: https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white
   :alt: Python 3.9 or newer

.. |podman| image:: https://img.shields.io/badge/Container-Podman-892CA0?logo=podman&logoColor=white
   :alt: Podman deployment

.. |opensearch| image:: https://img.shields.io/badge/OpenSearch-CCR-005EB8
   :alt: OpenSearch cross-cluster replication

|tests| |python| |podman| |opensearch|

OCI OpenSearch FSDR is a container-first controller for planned switchover,
failover, reverse protection, inventory and evidence collection for an OCI
OpenSearch CCR pair. It uses OCI Instance Principal to read one Vault Secret
that identifies both clusters and contains their internal OpenSearch
credentials.

The supported deployment uses Podman and a local-only HTTPS API on TCP 1604.
Its durable host installation is independent from the Git checkout. The web
console shows the FSDR phase, primary/follower
direction, protected indices, current document counts, CCR status, API jobs
and captured command output.

Quick start
-----------

On a new executor VM, clone the repository in any temporary directory and run
the installer::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /tmp/oci-opensearch-fsdr
    cd /tmp/oci-opensearch-fsdr
    sudo ./scripts/install-podman.sh --secret-id '<VAULT_SECRET_OCID>'
    rm -rf /tmp/oci-opensearch-fsdr

Open ``https://127.0.0.1:1604`` locally on the VM, or through an SSH tunnel.
The installer installs Podman, Python and its required host utilities on
dnf-based Linux, builds ``localhost/oci-opensearch-fsdr:local``, and stores
the operational files under ``/opt/oci-fsdr-opensearch``. The clone is used
only for the image build and can be removed after the command succeeds.

Documentation
-------------

* `Quick installation and update <docs/INSTALL.rst>`_
* `Container deployment, API and web console <docs/CONTAINER.rst>`_
* `FSDR workflow and operational commands <docs/FSDR.rst>`_
* `Recovery-path diagrams <docs/WORKFLOWS.md>`_
* `Future DR drill implementation plan <docs/DRILL_PLAN.rst>`_
* `Future multi-follower implementation plan <docs/MULTI_FOLLOWER_PLAN.rst>`_
* `Instance Principal IAM requirements <docs/IAM.rst>`_
* ``secret.example.json``: expected Vault Secret payload.
* ``docs/REMOTE_EVIDENCE.rst``: durable Object Storage evidence for ephemeral FSDR runners.

Cada operação de recuperação recebe um ciclo único, persistido localmente e
no Object Storage até ``reprotect`` concluir. Inventário agendado somente
mantém a evidência de início e não abre um ciclo de DR.

Scope and safety
----------------

Each protected index must declare ``mappings._meta.fsdr.replicate: true`` on
the leader and follower. The controller refuses DR operations when the marker
and discovered CCR scope differ. API requests for switchover, failover and
reprotect execute immediately when submitted.

The separate lab/demo setup remains isolated under ``lab/`` and is not part of
the controller image, Podman deployment, runtime volumes or API.

Validation
----------

Run the offline unit tests from a development environment::

    python3 -m unittest discover -s tests -v

The tests do not replace controlled validation of OCI IAM, Vault, private DNS,
network paths, TLS, CCR permissions, OpenSearch roles and the FSDR plan.
