IAM do executor OCI OpenSearch FSDR
====================================

Este documento é a referência canônica das permissões OCI usadas pelo
controller. Ele cobre somente a identidade da VM que executa o container
(``Instance Principal``); não descreve as permissões do usuário humano que
instala a solução nem as permissões internas do OpenSearch.

Princípio de menor privilégio
-----------------------------

O controller não cria, altera ou remove recursos OCI. Ele apenas lê o Secret e
os metadados dos clusters e, quando configurado, publica ou lê evidências no
bucket privado. A execução de CCR, alterações de índices e bloqueios de escrita
ocorrem pela API OpenSearch com as credenciais obtidas do Vault.

Crie o Dynamic Group
--------------------

Use um Dynamic Group exclusivo, por exemplo ``fsdr-opensearch-runner-dg``. Para
uma VM executora, associe somente seu OCID::

    ALL {instance.id = '<VM_OCID>'}

Se duas VMs executoras forem autorizadas, use os OCIDs explicitamente::

    ANY {instance.id = '<VM_FRANKFURT_OCID>', instance.id = '<VM_LONDON_OCID>'}

A regra do Dynamic Group habilita o Instance Principal. Ela não concede acesso
por si só; os statements abaixo precisam estar em uma policy criada no nível
raiz da tenancy. Em Identity Domains, qualifique o nome quando necessário, por
exemplo ``'<IDENTITY_DOMAIN_NAME>/fsdr-opensearch-runner-dg'``.

Statements obrigatórios
-----------------------

1. Leitura do Secret do Vault
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

    Allow dynamic-group fsdr-opensearch-runner-dg to read secret-bundles in compartment id <SECRET_COMPARTMENT_OCID> where target.secret.id = '<SECRET_OCID>'

**Motivo:** permite somente ``GetSecretBundle`` para o Secret configurado,
sempre na versão ``CURRENT``. O Secret contém os OCIDs dos clusters e as
credenciais de serviço do OpenSearch.

**Componente e ação:** ``fsdr_opensearch.cloud.Cloud.credentials()`` chama a
API OCI ``GetSecretBundle`` durante ``inventory``, validações, transições,
reprotect e a observação do dashboard. Sem esta linha, a execução falha antes
da descoberta dos clusters.

**Limite aplicado:** o filtro ``target.secret.id`` impede acesso a outros
Secrets do mesmo compartment. Não use ``read secret-family`` nem remova o
filtro sem necessidade operacional.

2. Descoberta dos clusters OpenSearch
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

    Allow dynamic-group fsdr-opensearch-runner-dg to read opensearch-clusters in compartment id <CLUSTER_COMPARTMENT_OCID>

Repita a linha para cada compartment que contenha um dos clusters. Se ambos
estiverem no mesmo compartment, uma única linha basta::

    Allow dynamic-group fsdr-opensearch-runner-dg to read opensearch-clusters in compartment id <FRANKFURT_CLUSTER_COMPARTMENT_OCID>
    Allow dynamic-group fsdr-opensearch-runner-dg to read opensearch-clusters in compartment id <LONDON_CLUSTER_COMPARTMENT_OCID>

**Motivo:** permite obter FQDN, endereço privado, versão, estado do cluster e
as conexões cross-cluster já configuradas. Esses dados identificam os dois
lados do par e validam a direção do CCR.

**Componente e ação:** ``fsdr_opensearch.cloud.Cloud.cluster()`` chama
``GetOpensearchCluster`` durante ``discover`` e ``bootstrap``. A mesma
informação é reutilizada pelo monitor do dashboard depois que o cache expira.

**Limite aplicado:** ``read`` não permite criar, modificar, iniciar, parar ou
remover clusters nem conexões OCI. O controller também não chama APIs OCI para
alterar conexões cross-cluster.

3. Evidência e logs no Object Storage (opcional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inclua este statement somente quando ``evidence_bucket`` estiver configurado::

    Allow dynamic-group fsdr-opensearch-runner-dg to manage objects in compartment id <EVIDENCE_BUCKET_COMPARTMENT_OCID> where all {target.bucket.name = '<EVIDENCE_BUCKET>', target.object.name = '<EVIDENCE_PREFIX>/*', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_OVERWRITE', request.permission = 'OBJECT_READ'}}

Substitua ``<EVIDENCE_PREFIX>`` pelo valor de ``evidence_prefix``; o padrão do
instalador é ``oci-fsdr-opensearch``.

**Motivo:** cada job publica ``bundle.tar.gz``, ``manifest.json`` e o log
legível ``controller.log`` no diretório imutável da execução. A API pode ler
``controller.log`` do bucket quando a cópia local já não existir.

**Componente e ação:**

* ``fsdr_opensearch.evidence._publish_one()`` usa ``PutObject`` para publicar
  o bundle, manifest e log;
* ``fsdr_opensearch.evidence.download_controller_log()`` usa ``GetObject``
  como fallback de ``GET /v1/jobs/<job-id>/output``;
* ``fsdr_opensearch.cloud.Cloud.object_storage()`` usa ``GetNamespace`` para
  descobrir o namespace da tenancy. Essa chamada não requer uma policy de
  objeto adicional para a própria tenancy.

**Permissões individuais:**

* ``OBJECT_CREATE``: primeira entrega de cada objeto, cujo nome é único;
* ``OBJECT_OVERWRITE``: nova tentativa após uma falha parcial de upload;
* ``OBJECT_READ``: consulta programática do ``controller.log`` pelo endpoint
  da API quando a cópia local não estiver disponível.

O statement não concede listagem, remoção, alteração de bucket, criação de
bucket, criação de PAR ou administração de retention rules. O operador ou a
equipe de recuperação deve ter uma policy separada de leitura para consultar o
bucket diretamente.

Configurações externas e permissões que não pertencem ao script
---------------------------------------------------------------

* **OpenSearch:** o usuário no Secret precisa de permissões no Security plugin
  para as APIs de CCR, índices, settings e estatísticas usadas pelo workflow.
  Isto não é IAM OCI.
* **Rede:** DNS privado, NSGs, Security Lists, Service Gateway ou Private
  Endpoint e rotas devem permitir a VM alcançar Vault, Object Storage e os
  endpoints OpenSearch. O controller não chama APIs OCI de rede.
* **KMS em bucket com CMK:** quando o bucket usa chave gerenciada pelo cliente,
  conceda ao serviço Object Storage as permissões KMS exigidas pela política da
  organização. O Instance Principal não chama Vault KMS diretamente.
* **Instalação:** ``install-podman.sh`` instala pacotes, cria diretórios e
  executa Podman no host. Essas ações usam ``sudo`` local, não IAM OCI.
* **FSDR externo:** permissões para OCI FSDR, Run Command, Functions ou outra
  automação que invoque ``fsdr-job.sh`` são independentes da identidade do
  processo Python dentro da VM.

Validação operacional
---------------------

Depois de aplicar a policy, valide com um inventário::

    /opt/oci-fsdr-opensearch/bin/fsdr-job.sh inventory

Com bucket configurado, verifique também que o job mostra
``remote_evidence.controller_log`` em ``GET /v1/jobs/<job-id>`` e que
``/opt/oci-fsdr-opensearch/bin/fsdr-job.sh log <job-id>`` retorna o conteúdo.
Um erro ``OCI GetSecretBundle failed`` normalmente indica o primeiro statement;
um erro ``GetOpensearchCluster`` indica o segundo; e ``PutObject`` ou
``GetObject`` indica o terceiro ou conectividade com Object Storage.

Referências oficiais
--------------------

* `Dynamic Groups <https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/Writing_Matching_Rules_to_Define_Dynamic_Groups.htm>`_
* `Vault, Key Management and Secrets policy details <https://docs.oracle.com/en-us/iaas/Content/Identity/policyreference/keypolicyreference.htm>`_
* `OpenSearch IAM permissions <https://docs.oracle.com/en-us/iaas/Content/search-opensearch/Concepts/ocisearchpermissions.htm>`_
* `Object Storage security and policy conditions <https://docs.oracle.com/en-us/iaas/Content/Security/Reference/objectstorage_security.htm>`_
