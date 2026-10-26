# Comprovantes financeiros privados

`Expense.proof_asset` e `ManualPaymentEvidence.proof_asset` usam `PRIVATE_MEDIA_ROOT`, fora das pastas públicas `MEDIA_ROOT` e `STATIC_ROOT`. Essa pasta não pode ter `alias`, `root` ou rota de arquivos estáticos no proxy. Arquivos novos recebem permissão `0600` e diretórios `0700` no Linux; o usuário do serviço Django precisa ser o proprietário.

O armazenamento não fornece `.url`. As telas usam `/gestao/eventos/<evento>/documentos/<tipo>/<documento>/`, que exige conta ativa com permissão financeira do evento, verifica o vínculo do documento e registra auditoria. A resposta força download, `no-store`, tipo binário e `nosniff`. O administrador Django mantém o upload e mostra um link protegido para baixar o arquivo existente.

## Arquivos anteriores à mudança

As migrations alteram apenas a configuração dos campos e não movimentam arquivos. Antes do deploy, bloqueie no Nginx os prefixos públicos `/media/expenses/receipts/` e `/media/evidences/`, incluindo homologação e quaisquer aliases de mídia.

Execute com o ambiente e usuário do serviço de cada instalação:

```text
python manage.py migrate_private_documents
python manage.py migrate_private_documents --apply
```

O primeiro comando é uma simulação. O segundo copia somente os arquivos referenciados no banco para a pasta privada, verifica SHA-256 dos dois lados e então remove a cópia pública. Não modifica referências no banco. Conflitos de conteúdo e arquivos ausentes são reportados; não há sobrescrita de cópias diferentes. O comando pode ser executado novamente. Preserve backup restrito e mantenha os bloqueios públicos também para arquivos órfãos que não tenham referência no banco.

Cadastros e uploads devem ficar pausados durante a migração de arquivos existentes. Se executado como `root`, ajuste o proprietário de `PRIVATE_MEDIA_ROOT` para o usuário do serviço antes de reabrir a aplicação. Verifique download autorizado, bloqueio entre eventos e resposta 404 das URLs públicas antigas.
