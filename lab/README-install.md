# OCI OpenSearch CCR Lab — instalação rápida

Este pacote é pontual: extraia em `/opt/oci-opensearch-lab`, execute-o e remova
o diretório quando a demonstração terminar. Ele não instala serviço nem altera
os arquivos do FSDR.

## 1. Atualizar o inventário FSDR

```bash
sudo -u fsdrsvc /opt/oci-opensearch-fsdr/.venv/bin/python \
  /opt/oci-opensearch-fsdr/fsdr.py inventory --human
```

## 2. Extrair o pacote

```bash
sudo tar -xzf oci-opensearch-lab.tar.gz -C /opt
cd /opt/oci-opensearch-lab
sha256sum -c SHA256SUMS
sudo chown -R fsdrsvc:fsdrsvc /opt/oci-opensearch-lab
```

Confirme os acessos:

```bash
sudo -u fsdrsvc test -r /etc/oci-opensearch-fsdr/config.json
sudo -u fsdrsvc test -r /var/lib/oci-opensearch-fsdr/state.json
sudo -u fsdrsvc test -w /opt/oci-opensearch-lab
```

## 3. Criar os índices de demonstração

```bash
python3 setup_demo.py --indices 3 --docs-per-index 1000
```

O wrapper solicita `sudo`, reinicia como `fsdrsvc` e, na primeira execução,
pergunta se deve criar `.venv` e instalar os requisitos. O lab usa a
configuração, Secret e inventário existentes do FSDR.

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

## 4. Abrir o dashboard

```bash
python3 monitor.py
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
python3 setup_demo.py --cleanup

# Para o CCR de cada par, bloqueia o writer e remove follower e leader.
python3 setup_demo.py --cleanup --execute

# Atualiza o manifesto FSDR, pois os índices de demonstração não existem mais.
sudo -u fsdrsvc /opt/oci-opensearch-fsdr/.venv/bin/python \
  /opt/oci-opensearch-fsdr/fsdr.py inventory --human
```

A limpeza opera somente sobre índices registrados no estado local do lab,
cujo nome pertence ao prefixo configurado e cuja marca
``_meta.fsdr.demo_uuid`` coincide, além de exigir
``_meta.fsdr.replicate: true``. UUID interno do índice não é usado como
critério de limpeza, pois reprotect recria o follower com um UUID novo. Ela é
bloqueada durante uma transição FSDR e pode ser repetida para retomar após uma
falha de comunicação. O ``demo_uuid`` é persistente por instalação do lab, em
``var/lab/state/demo-identity.json``; não remova esse arquivo se pretende criar
outro dataset com a mesma identidade.
