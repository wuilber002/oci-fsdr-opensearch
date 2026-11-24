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

Para uma VM efêmera, inclua também ``--evidence-bucket '<BUCKET_PRIVADO>'``.
O controller envia ao Object Storage os artefatos de recuperação e auditoria
sem enviar credenciais. Consulte ``docs/REMOTE_EVIDENCE.rst`` antes de apagar
a VM.

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

Desinstalação para testes limpos
--------------------------------

Para repetir a instalação inicial sem reutilizar inventário, histórico, token,
certificados ou configuração anterior, execute no clone temporário::

    sudo ./scripts/install-podman.sh --uninstall

O comando remove o container ``oci-opensearch-fsdr``, a imagem local
``localhost/oci-opensearch-fsdr:local`` e todo o conteúdo de
``/opt/oci-fsdr-opensearch``. Podman e as dependências do host permanecem
instalados para que a instalação seguinte seja rápida.
