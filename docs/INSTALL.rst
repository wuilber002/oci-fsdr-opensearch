Instalação e atualização rápidas
================================

Pré-requisitos
--------------

O host precisa estar em uma distribuição Linux baseada em ``dnf``, com acesso
à Internet para instalar pacotes e baixar a imagem base, Instance Principal
configurado, e conectividade com OCI Vault e os endpoints privados dos dois
clusters OpenSearch.

Instalação inicial
------------------

Informe o OCID do segredo no Vault que contém a configuração dos clusters e
execute::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /tmp/oci-fsdr-opensearch
    cd /tmp/oci-fsdr-opensearch
    sudo ./scripts/install-podman.sh --secret-id '<VAULT_SECRET_OCID>'
    cd /
    sudo rm -rf /tmp/oci-fsdr-opensearch

O instalador prepara Podman e as dependências, cria a imagem local e inicia o
controller. Os arquivos operacionais persistem em
``/opt/oci-fsdr-opensearch``; o clone temporário pode ser removido.

Verificação rápida::

    podman ps --filter name=oci-opensearch-fsdr
    /opt/oci-fsdr-opensearch/bin/fsdr-job.sh inventory

Abra o dashboard local em ``https://127.0.0.1:1604``. Para acesso remoto, use
um túnel SSH::

    ssh -L 1604:127.0.0.1:1604 opc@<IP_DO_HOST>

Atualização
-----------

A atualização mantém a configuração, histórico, inventário, token e
certificados existentes. Use um novo clone temporário da revisão desejada::

    sudo dnf install -y git
    git clone https://github.com/wuilber002/oci-fsdr-opensearch.git /tmp/oci-fsdr-opensearch-update
    cd /tmp/oci-fsdr-opensearch-update
    git checkout main
    sudo ./scripts/install-podman.sh --update
    cd /
    sudo rm -rf /tmp/oci-fsdr-opensearch-update

Para atualizar para uma versão ou commit específico, substitua ``git checkout
main`` por ``git checkout <TAG_OU_COMMIT>``. Faça backup de
``/opt/oci-fsdr-opensearch/runtime`` antes de uma atualização que altere a
compatibilidade do estado do workflow.

Instalação limpa a partir da versão anterior
--------------------------------------------

Este procedimento remove o controller anterior, seu histórico, inventário,
token e certificados. Faça uma cópia do estado caso ele seja necessário para
auditoria ou rollback::

    sudo tar -C /opt -czf /var/tmp/oci-fsdr-opensearch-runtime-backup.tgz \
      oci-opensearch-fsdr/runtime oci-fsdr-opensearch/runtime 2>/dev/null || true

Pare e remova somente os containers e imagens conhecidos do FSDR. Estes
comandos não removem Docker, Podman, nem containers de outros projetos::

    sudo docker rm -f oci-opensearch-fsdr 2>/dev/null || true
    sudo podman rm -f oci-opensearch-fsdr 2>/dev/null || true
    sudo docker image rm oci-opensearch-fsdr:local 2>/dev/null || true
    sudo podman image rm localhost/oci-opensearch-fsdr:local 2>/dev/null || true
    sudo rm -rf /opt/oci-opensearch-fsdr /opt/oci-fsdr-opensearch

Em seguida, execute a instalação inicial desta página. O primeiro
``inventory`` será criado como um novo ciclo FSDR e não reutilizará qualquer
evidência da instalação anterior.
