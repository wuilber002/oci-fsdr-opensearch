Instance Principal para a VM executora
=====================================

Dynamic Group sugerido: ``fsdr-opensearch-runner-dg``.
Para uma VM, use esta matching rule::

    ALL {instance.id = '<VM_OCID>'}

Para executores nas duas regiões, use esta alternativa::

    ANY {instance.id = '<VM_FRANKFURT_OCID>', instance.id = '<VM_LONDON_OCID>'}

Crie a policy no root da tenancy, delimitada aos compartments e ao Secret::

    Allow dynamic-group fsdr-opensearch-runner-dg to read secret-bundles in compartment id <SECRET_COMPARTMENT_OCID> where target.secret.id = '<SECRET_OCID>'
    Allow dynamic-group fsdr-opensearch-runner-dg to read opensearch-clusters in compartment id <FRANKFURT_CLUSTER_COMPARTMENT_OCID>
    Allow dynamic-group fsdr-opensearch-runner-dg to read opensearch-clusters in compartment id <LONDON_CLUSTER_COMPARTMENT_OCID>

Se os dois clusters estiverem no mesmo compartment, uma linha para esse
compartment é suficiente. Para Dynamic Group em outro identity domain, use
``'<IDENTITY_DOMAIN_NAME>/fsdr-opensearch-runner-dg'`` nas linhas da policy.

Essas permissões cobrem a descoberta, sem criar recursos. Operações de CCR e
índices são autorizadas pelo usuário interno do OpenSearch obtido no Secret.
Não é necessário conceder manage de rede ou de clusters à VM para esse fluxo.
Conexões OCI devem estar previamente configuradas. Alterá-las pelo SDK seria
uma funcionalidade diferente com permissões adicionais, ainda não implementada.

Evidência remota para VM efêmera
--------------------------------

Se ``evidence_bucket`` estiver configurado, acrescente somente a permissão de
criar e repetir a entrega no bucket privado dedicado::

    Allow dynamic-group fsdr-opensearch-runner-dg to manage objects in compartment id <EVIDENCE_BUCKET_COMPARTMENT_OCID> where all {target.bucket.name = '<EVIDENCE_BUCKET>', target.object.name = 'oci-fsdr-opensearch/*', any {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_OVERWRITE'}}

Veja ``docs/REMOTE_EVIDENCE.rst`` para o conteúdo exportado, criptografia,
retenção e o procedimento seguro de retomada.

Configure a VM com acesso ao metadata service, aos endpoints OCI e aos dois
endpoints privados OpenSearch. O Secret precisa estar disponível durante o DR.
As políticas para o serviço FSDR acionar Run Command são independentes destas
permissões do processo Python que roda na VM.

Referências
-----------

* Dynamic Groups: https://docs.oracle.com/en-us/iaas/Content/Identity/dynamicgroups/Writing_Matching_Rules_to_Define_Dynamic_Groups.htm
* OpenSearch IAM: https://docs.oracle.com/en-us/iaas/Content/search-opensearch/Concepts/ocisearchpermissions.htm
* Secret específico: https://docs.oracle.com/en-us/iaas/Content/APIGateway/Tasks/apigatewaytroubleshooting_topic-gateway_create_or_update_fails.htm
