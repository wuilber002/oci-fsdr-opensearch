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

Cada ciclo DR recebe um ID único, criado quando a primeira solicitação aceita
inicia o fluxo. O formato combina a data UTC e o ID do job inicial, por
exemplo ``dr-20261124T020415Z-9be15e83c51b``. Todas as execuções pertencentes
ao ciclo são publicadas em um caminho único como
``<prefix>/cycles/<cycle-id>/executions/<UTC>-<uuid>/``. O manifesto contém
hash SHA-256, tamanho e lista dos arquivos. O bundle contém:

* ``report.json`` com comando, resultado, fase, duração, RPO e inventário;
* ``state.json`` com a fase, primário ativo, transição, checkpoints e escopo
  de índices necessários para análise e retomada controlada;
* ``dr-cycle.json`` com o ID, rota, job inicial, ciclo ativo/concluído ou
  invalidado e a validade do inventário que admitiu o switchover;
* ``workflow.json`` com a sequência e os gates de cada passo;
* ``inventory-health.json`` e ``last-run.json``;
* ``results/<job-id>.json`` com o resultado estruturado daquela execução;
* ``controller.log`` como arquivo texto separado, com o progresso, as
  validações, os clusters consultados e o traceback seguro quando houver falha.
  O mesmo objeto também pode ser lido pela API do controller;
* ``discovery.json`` com a identidade dos clusters, endpoints e aliases CCR;
* registros ``history/evidence-*.json`` que fixam os hashes de transições
  concluídas.

O bundle deliberadamente não contém o payload do Vault, senhas OpenSearch,
token da API local, chave privada TLS, variáveis de ambiente ou os logs JSONL
brutos. O ``controller.log`` legível é redigido e publicado tanto dentro do
bundle quanto como objeto próprio no mesmo diretório da execução, para
consulta direta sem extrair o arquivo compactado. Esses dados sensíveis nunca
são copiados para o bucket.

Consulta programática do log
-----------------------------

Após a entrega, o metadado do job registra o objeto ``controller.log``. A rota
``GET /v1/jobs/<job-id>/output`` consulta primeiro o arquivo local e, se ele
não estiver mais disponível, obtém a cópia do Object Storage. A resposta traz
``X-FSDR-Log-Source: local`` ou ``object-storage``. Assim, um consumidor não
precisa listar o bucket nem conhecer o caminho do ciclo.

Ciclo único por par de clusters
-------------------------------

Há somente um ciclo ativo por par de clusters. ``inventory`` manual ou
``validate-switchover`` abre ou continua o ciclo de planned switchover;
``failover`` abre ou continua o ciclo de emergency failover. Jobs rejeitados
antes da execução não criam um ciclo. Inventário agendado apenas atualiza a
evidência que permite iniciar por ``validate-switchover`` enquanto tiver menos
de uma hora; ele nunca abre um ciclo.

Antes do ``switchover``, um ciclo planejado é invalidado quando vence a
validade de uma hora do inventário admitido. Um inventário manual posterior
abre um novo ciclo. Após ``switchover`` ou ``failover`` o ciclo não expira: a
mesma identificação permanece até um ``reprotect`` bem-sucedido. Falhas e
retomadas pertencem ao ciclo já existente.

Evidências de inventário agendado ficam fora de ``cycles/``, em
``<prefix>/maintenance/<pair-identity>/``. Isso mantém o bucket, dedicado
exclusivamente ao FSDR, auditável sem confundir manutenção automática com uma
operação de recuperação.

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

As permissões IAM do executor, o motivo de cada statement e a relação com os
componentes do controller estão consolidados em ``docs/IAM.rst``. Em especial,
a API que recupera ``controller.log`` do bucket exige ``OBJECT_READ`` além de
``OBJECT_CREATE`` e ``OBJECT_OVERWRITE``. Não conceda ``manage buckets``,
listagem ou remoção de objetos ao executor.

Retomada em outra VM
--------------------

O bundle preserva a evidência e o estado necessários para uma retomada
auditada. A VM nova ainda deve ser instalada com o mesmo Secret OCID, acesso
ao bucket e Instance Principal. Antes de qualquer ``--resume``, o operador
deve comparar a identidade do par, a transição e os hashes do manifesto com o
ambiente atual. A importação automática do estado remoto não é feita pelo
controller: isso evita que uma VM nova continue uma transição sem revisão
humana nem validação dos clusters.
