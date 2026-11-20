# OCI OpenSearch CCR — laboratório

Este pacote é separado do runtime FSDR. Extraia-o em
`/opt/oci-opensearch-lab` e execute os utilitários pontualmente; ele não cria
serviço nem lê arquivos, estado ou inventário do FSDR.

Requer Python 3.9+, `venv`/`ensurepip`, `curses`, acesso ao índice Python e uma
VM OCI com Instance Principal. O Secret informado ao lab contém os dois OCIDs
de cluster e as credenciais OpenSearch. O SDK consulta OCI para
atualizar FQDN, IP privado, versão e aliases CCR; senhas nunca são escritas no
disco.

O Dynamic Group da VM precisa de permissão para ler o Secret Bundle e consultar
os dois OpenSearch clusters. As permissões de OpenSearch para criar índices e
operar CCR continuam sendo as do usuário armazenado no Secret.

Preparação
----------

Extraia e prepare o ambiente virtual no diretório extraído:

```bash
tar -xzf oci-opensearch-lab.tar.gz
cd oci-opensearch-lab
sha256sum -c SHA256SUMS
```

Os wrappers executam com o usuário que os invocou. Esse usuário deve possuir o
diretório do lab para criar `.venv` e `var/lab/`.

Na primeira execução, `setup_demo.py` ou `monitor.py` procura `.venv`. Se ele
não existir, pergunta se deve criá-lo e instalar `requirements.txt`. Responda
`s` para prosseguir. Ao responder `n`, o comando encerra porque o lab não pode
executar sem seu virtualenv.

O lab grava somente seus arquivos descartáveis no diretório atual:
`var/lab/state` e `var/lab/log`. Informe o Secret com `--secret-id` ou defina
`OPENSEARCH_LAB_SECRET_ID`. `--use-private-ip` é o padrão; use
`--no-use-private-ip` somente se o FQDN dos clusters puder ser resolvido e
alcançado pela VM. A ferramenta preserva o FQDN para SNI e validação TLS.

As permissões e as conexões devem estar preparadas nos dois sentidos. Não altere
para simular uma promoção. O loader não usa mais ``hooks``/``hook_timeout`` de
arquivos antigos; esses campos podem ser removidos da configuração do lab.

Criação de índices e documentos
------------------------------

Execute a partir da raiz do projeto::

    python3 setup_demo.py --secret-id <OCID_DO_SECRET> --indices 10 --docs-per-index 10000

Opções: ``--batch-size 500 --shards 1 --replicas 1``. Zero documentos é aceito.
O setup cria somente índices novos no prefixo configurado, usa Bulk com IDs
determinísticos e registra cada resultado. Inicia CCR, compara checkpoints e
confere contagens após refresh. Antes da primeira criação, descobre os dois
clusters no OCI e infere o primary pela direção CCR ao vivo dos índices que já
possuem ``_meta.fsdr.replicate: true``. Se não existir nenhum follower marcado,
usa o cluster ``a`` como origem determinística. Identifica o dataset por ``mappings._meta`` e
salva seus UUIDs em estado exclusivo do lab. Os índices criados pelo lab recebem
``_meta.fsdr.replicate: true`` e um ``_meta.fsdr.demo_uuid`` aleatório de 64
caracteres hexadecimais. O valor é gerado uma única vez na primeira execução
do lab e preservado em ``var/lab/state/demo-identity.json``. Portanto, todos
os datasets e índices criados por esse mesmo lab, mesmo após cleanup, recebem
a mesma marca. O valor também é salvo no estado do dataset e replicado para o
follower pelo CCR. A marca
``replicate`` é requisito para que o inventário FSDR aceite o índice como parte
do escopo protegido.

Setup interrompido pode ser retomado repetindo os mesmos parâmetros. Um lote
cuja resposta foi perdida é reaplicado sem duplicar IDs. Setup concluído não
pode ser reexecutado com o mesmo total, pois isso não cria nem sobrescreve
índices. Para expandir o dataset, informe em ``--indices`` o total desejado.
Com cinco índices existentes, ``--indices 12`` cria ``fsdr-demo-0006`` até
``fsdr-demo-0012``, preserva os cinco anteriores e configura CCR somente para
os sete novos. ``docs-per-index``, ``shards`` e ``replicas`` devem ser iguais
aos do dataset original. Ao concluir a expansão, execute o inventário FSDR
antes de usar os novos índices em um switchover, failover ou reprotect.
Não há exclusão automática. Não use o mesmo diretório de estado para outro
dataset.

Limpeza do dataset
------------------

``python3 setup_demo.py --cleanup`` mostra o plano sem alterar os clusters.
``python3 setup_demo.py --cleanup --execute`` bloqueia a escrita no leader,
para o CCR no follower e remove ambos os índices de cada par. A operação usa
somente o estado do lab e confirma o prefixo, ``_meta.fsdr.demo_uuid`` e
``_meta.fsdr.replicate: true`` antes de cada exclusão. UUID interno não é
critério de limpeza, pois a reconstrução do follower altera esse identificador.
Se for interrompida, repita o mesmo comando para retomá-la. Ao terminar, execute ``fsdr.py
inventory`` antes de qualquer operação FSDR, pois o manifesto anterior ainda
continha os índices removidos.

Dashboard
---------

Em um terminal interativo com TERM configurado::

    python3 monitor.py

* F4: criar um lote de 100 a 300 documentos em **cada** índice do lab. A
  quantidade é sorteada individualmente por índice.
* F8: excluir aleatoriamente até a mesma faixa de documentos em **cada** índice.
  Se um índice tiver menos documentos disponíveis, remove somente os encontrados.
* Q: sair após terminar a operação de escrita em andamento.
* Setas: rolar a lista.

O dashboard não mantém carga contínua. Cada tecla enfileira um único lote e só
aceita outro quando não houver lote pendente ou em andamento. Criações usam Bulk;
exclusões selecionam documentos aleatoriamente e usam Bulk. Cada lote, suas
quantidades por índice e qualquer falha são registrados.

O gerador observa a CCR dos índices do dataset ao vivo para identificar o
writer, sem ler estado FSDR. Entre promoção e reconstrução, exige exatamente
um lado gravável. Bloqueios, troca parcial, indisponibilidade ou ambiguidade
pausam a carga. No novo primary, confere a marca do dataset e UUID antes de escrever.
O controle também funciona após o antigo primary ser recriado como follower.
O bloqueio de escrita do DR impede novas escritas na origem durante a troca;
não há dependência de um lock de laboratório dentro do script FSDR.

O dashboard mostra somente os índices que pertencem ao dataset do laboratório:
o total de índices e documentos de cada cluster e uma linha lado a lado para
cada par replicado. Ele não consulta ``_cat/indices/*`` nem tenta contabilizar
índices externos ao lab, pois isso exigiria permissões adicionais e não ajuda a
demonstração. Amostras parciais exibem erro na linha afetada.

Contagens dependem do refresh e são coletadas em momentos diferentes: não são
uma comparação transacional nem prova de igualdade dos dados. O atraso não é
convertido em segundos. Muitos índices tornam ``_count`` por índice custoso;
esta UI é uma ferramenta de demonstração, não um coletor de observabilidade em
escala.

Após uma troca, pressione F4 ou F8 para atuar no writer observado.
O dashboard não promove clusters, não inicia DR nem altera DNS/LB.

Logging e estado
----------------

Logs JSONL separados por execução, com rotação a 20 MB e dez backups; não se
apagam automaticamente arquivos de execuções anteriores. Senhas do ambiente
são mascaradas. Não são registrados Authorization ou corpos de documentos.
Os novos arquivos usam umask 077. Logs do dashboard vão só para arquivo.
O setup mostra progresso curto por índice e durante a sincronização inicial.
Eventos detalhados permanecem no JSONL.

O monitor observa a topologia CCR diretamente em cada amostra. Durante uma
troca ou reconstrução, a topologia ambígua ou indisponível bloqueia F4 e F8 sem
precisar consultar o controlador FSDR. Após a conclusão, identifica o novo
primary diretamente pela topologia CCR.

O arquivo `var/lab/state/discovery.json` registra somente metadados OCI
necessários ao lab, sem senhas, e `state.json` identifica os índices criados
pela demonstração. Nenhum arquivo do FSDR é lido ou alterado.
