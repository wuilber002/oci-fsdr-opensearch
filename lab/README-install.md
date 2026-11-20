# OCI OpenSearch CCR Lab — instalação rápida

Este pacote é pontual: extraia em `/opt/oci-opensearch-lab`, execute-o e remova
o diretório quando a demonstração terminar. Ele não instala serviço nem altera
os arquivos do FSDR.

## 1. Extrair o pacote

```bash
sudo tar -xzf oci-opensearch-lab.tar.gz -C /opt
cd /opt/oci-opensearch-lab
sha256sum -c SHA256SUMS
sudo chown -R "$USER":"$USER" /opt/oci-opensearch-lab
```

Confirme que a VM possui Instance Principal com acesso ao Secret e aos dois
clusters OCI. Não são necessários arquivos, usuário de serviço ou inventário do
FSDR.

## 2. Criar os índices de demonstração

```bash
python3 setup_demo.py \
  --secret-id '<OCID_DO_SECRET>' \
  --indices 3 --docs-per-index 1000
```

Na primeira execução, o wrapper pergunta se deve criar `.venv` e instalar os
requisitos. O lab usa somente o Secret OCI, Instance Principal e CCR ao vivo.

`--indices` é o total desejado para o dataset, não a quantidade a adicionar.
Por exemplo, se já existem cinco índices e a demonstração precisa de doze:

```bash
python3 setup_demo.py --indices 12 --docs-per-index 1000
```

O comando preserva `fsdr-demo-0001` a `fsdr-demo-0005`, cria
`fsdr-demo-0006` a `fsdr-demo-0012` e inicia CCR somente para os novos
índices. `docs-per-index`, `shards` e `replicas` devem manter os valores do
dataset original. Ao terminar, atualize o inventário do FSDR para incluir os
novos índices antes de qualquer operação de DR.

## 3. Abrir o dashboard

```bash
python3 monitor.py --secret-id '<OCID_DO_SECRET>'
```

- **F4** cria de 100 a 300 documentos aleatórios em cada índice do lab.
- **F8** remove de 100 a 300 documentos aleatórios em cada índice do lab.
- **Q** fecha o dashboard.

Para remover o lab ao final, saia do dashboard e apague somente o diretório:

```bash
sudo rm -rf /opt/oci-opensearch-lab
```

Antes de remover o diretório, remova o dataset de demonstração de forma segura:

```bash
# Exibe os índices que serão removidos, sem alterar os clusters.
python3 setup_demo.py --secret-id '<OCID_DO_SECRET>' --cleanup

# Para o CCR de cada par, bloqueia o writer e remove follower e leader.
python3 setup_demo.py --secret-id '<OCID_DO_SECRET>' --cleanup --execute

# Atualiza o manifesto FSDR, pois os índices de demonstração não existem mais.
sudo -u fsdrsvc /opt/oci-opensearch-fsdr/.venv/bin/python \
  /opt/oci-opensearch-fsdr/fsdr.py inventory --human
```

A limpeza opera somente sobre índices registrados no estado local do lab,
cujo nome pertence ao prefixo configurado e cuja marca
``_meta.fsdr.demo_uuid`` coincide, além de exigir
``_meta.fsdr.replicate: true``. UUID interno do índice não é usado como
critério de limpeza, pois reprotect recria o follower com um UUID novo. Ela
pode ser repetida para retomar após uma falha de comunicação. O ``demo_uuid`` é
persistente por instalação do lab, em
``var/lab/state/demo-identity.json``; não remova esse arquivo se pretende criar
outro dataset com a mesma identidade.
