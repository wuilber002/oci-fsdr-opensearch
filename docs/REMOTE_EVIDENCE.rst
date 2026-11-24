Evidência remota para executores efêmeros
==========================================

Uma VM FSDR efêmera não é uma fonte confiável de histórico nem de estado de
recuperação. Configure um bucket privado OCI Object Storage para receber uma
evidência imutável ao final de cada comando, inclusive quando o comando falha.
O controller grava primeiro uma cópia privada no ``evidence-outbox`` local e
só a remove depois que o upload conclui. Uma indisponibilidade do bucket não
desfaz uma operação OpenSearch já concluída; o item fica pendente e é tentado
novamente no próximo job.

Conteúdo de cada bundle
-----------------------

Cada execução envia ``manifest.json`` e ``bundle.tar.gz`` em um caminho único
como ``<prefix>/<pair-identity>/executions/<UTC>-<uuid>/``. O manifesto contém
hash SHA-256, tamanho e lista dos arquivos. O bundle contém:

* ``report.json`` com comando, resultado, fase, duração, RPO e inventário;
* ``state.json`` com a fase, primário ativo, transição, checkpoints e escopo
  de índices necessários para análise e retomada controlada;
* ``workflow.json`` com a sequência e os gates de cada passo;
* ``inventory-health.json`` e ``last-run.json``;
* ``discovery.json`` com a identidade dos clusters, endpoints e aliases CCR;
* registros ``history/evidence-*.json`` que fixam os hashes de transições
  concluídas.

O bundle deliberadamente não contém o payload do Vault, senhas OpenSearch,
token da API local, chave privada TLS, variáveis de ambiente, logs brutos ou
saída bruta dos jobs. Esses dados não devem ser copiados para o bucket.

Configuração
------------

Na instalação ou atualização, informe o bucket privado::

    sudo ./scripts/install-podman.sh --secret-id '<VAULT_SECRET_OCID>' \
      --evidence-bucket '<BUCKET_PRIVADO>' \
      --evidence-prefix 'oci-fsdr-opensearch'

Em uma instalação existente, use o clone temporário normalmente e execute::

    sudo ./scripts/install-podman.sh --update \
      --evidence-bucket '<BUCKET_PRIVADO>' \
      --evidence-prefix 'oci-fsdr-opensearch'

O namespace é obtido pelo Instance Principal; não deve ser gravado como uma
configuração manual. Para um ambiente efêmero de produção, a configuração do
bucket deve fazer parte da instalação inicial e o descarte da VM deve ocorrer
somente após confirmar que não existem itens em
``/opt/oci-fsdr-opensearch/runtime/state/evidence-outbox``.

Segurança e retenção
--------------------

Crie um bucket privado dedicado, com versionamento ativado e política de
retenção/lifecycle compatível com a auditoria da organização. Use criptografia
em repouso do Object Storage; quando exigido, configure uma chave Vault/KMS
como chave de criptografia padrão do bucket. Mantenha o acesso pela rede
privada (Service Gateway ou Private Endpoint quando aplicável).

O Dynamic Group da VM deve ter somente permissão de criar ou sobrescrever o
objeto da evidência. Uma equipe de recuperação separada pode receber ``read
objects`` para consultar ou copiar os bundles durante uma retomada. Para o
prefixo acima, use uma policy no compartment do bucket::

    Allow dynamic-group fsdr-opensearch-runner-dg to manage objects in compartment id <EVIDENCE_BUCKET_COMPARTMENT_OCID> where all {target.bucket.name = '<BUCKET_PRIVADO>', target.object.name = 'oci-fsdr-opensearch/*', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_OVERWRITE'}}

``PutObject`` precisa de ``OBJECT_CREATE`` na primeira entrega e
``OBJECT_OVERWRITE`` numa nova tentativa do mesmo bundle. ``GetNamespace``
não exige permissão quando solicitado para a tenancy do chamador. Não conceda
``manage buckets``, leitura ou remoção de objetos ao executor. Em bucket com
chave KMS gerenciada pelo cliente, conceda também ao serviço Object Storage as
permissões KMS necessárias para criptografar objetos. Versionamento e retenção
protegem contra remoção acidental; os bundles FSDR usam nomes únicos e não
substituem uma execução anterior.

Retomada em outra VM
--------------------

O bundle preserva a evidência e o estado necessários para uma retomada
auditada. A VM nova ainda deve ser instalada com o mesmo Secret OCID, acesso
ao bucket e Instance Principal. Antes de qualquer ``--resume``, o operador
deve comparar a identidade do par, a transição e os hashes do manifesto com o
ambiente atual. A importação automática do estado remoto não é feita pelo
controller: isso evita que uma VM nova continue uma transição sem revisão
humana nem validação dos clusters.
