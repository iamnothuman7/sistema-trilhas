# Patrocinadores e financeiro por evento

O banco de patrocinadores reúne nome, logo (PNG/JPG/WebP até 15 MB), site, Instagram e contatos internos. Uma marca pode participar de vários eventos. Cada participação possui cota, ordem, autorização de exibição, valor combinado, prazo e observações financeiras próprios.

Novas participações começam ocultas no site. Marcar a autorização publica somente a marca e seus links na edição escolhida; valores, contatos e histórico nunca entram no site público. A autorização independe do recebimento. A migração preserva a publicação e os arquivos dos patrocinadores anteriores, cria seus registros centrais e deixa o valor a definir, sem inventar recebimentos nem fundir marcas com nomes parecidos.

## Operação

1. Abra **Banco de patrocinadores**, cadastre a marca e envie a logo.
2. Entre nos patrocinadores do evento e escolha **Vincular marca**. **Novo patrocinador** permite criar a marca e a participação juntas.
3. Defina o valor combinado e o prazo. Vazio significa negociação pendente; zero significa apoio sem cobrança.
4. Em **Acompanhar e dar baixa**, registre cada recebimento real: valor, data, forma e referência. O sistema calcula recebido e restante; saldo zero quita o patrocínio automaticamente.
5. Uma baixa incorreta pode ser revertida com confirmação e motivo. O registro original fica preservado, um lançamento negativo ajusta o financeiro principal e o saldo é reaberto. A ação é contábil; não efetua devolução bancária.
6. Exporte os saldos e o histórico pela lista ou por **Relatórios**. Ambos também fazem parte do ZIP completo.

## Integração financeira

- Cada baixa gera exatamente uma receita `RECEITA_PATROCINIO` no evento correspondente, na data informada para o recebimento.
- Cada reversão gera um lançamento `ESTORNO_PATROCINIO` negativo na data da correção. A receita líquida e o resultado principal já incluem ambos: não somar o resumo de patrocínios novamente.
- O financeiro principal mostra também os valores combinados e os saldos atuais a receber, fora do caixa confirmado.
- O filtro de período do extrato e dos recebimentos usa as respectivas datas de reconhecimento/recebimento. O relatório de saldos sempre mostra a posição atual de todos os acordos, conforme indicado no nome e no LEIA-ME do ZIP.
- Acordo, baixa, razão financeiro e auditoria usam transações. Chave única de envio impede baixa duplicada; um bloqueio de escrita por participação serializa alterações de saldo, inclusive no SQLite. Edição de acordo usa versão para rejeitar telas desatualizadas. Valores não podem ultrapassar o saldo nem ficar abaixo do já recebido.
- Não são criadas cobranças no Mercado Pago: a baixa confirma valores recebidos externamente, com confirmação explícita do operador.

## Acessos e preservação

Conteúdo gerencia marcas e publicação; financeiro consulta valores, altera acordos, registra/reverte baixas e exporta relatórios. Gestores têm ambas as capacidades. Eventos e organizações são verificados no servidor. Uma conta limitada a um evento não pode alterar a marca compartilhada com eventos fora de seu acesso.

Alterações de imagem reutilizam validação, prévia otimizada e compensação de arquivos em caso de rollback. Atualizações da marca preservam acordos, recebimentos e autorizações. Contatos e observações permanecem internos.

## Verificação

Testes cobrem baixas parciais/integral, idempotência, reversão, rollback, valores inválidos, edição desatualizada, escopo, permissões, publicação, sincronização de logos, exportações e migração dos registros anteriores. A verificação visual usa dados fictícios isolados em 360, 390, 768 e 1440 px, nos temas claro e escuro.
