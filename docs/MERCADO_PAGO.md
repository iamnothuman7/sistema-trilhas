# Mercado Pago — conta própria da Equipe Morcego

O checkout usa **Payments API `/v1/payments`** para Pix e cartão. Não existe aprovação simulada, token de demonstração ou QR fictício. Sem configuração válida, a página informa que o pagamento online está indisponível e não envia uma cobrança.

## Situação da ativação

A aplicação Mercado Pago da Equipe Morcego ainda precisa ser criada. As credenciais de outro cliente ou aplicação do Navie Vibe **não devem ser copiadas**. A implementação foi verificada com respostas HTTP simuladas em banco de testes; nenhuma cobrança real foi realizada. A ativação e homologação da conta própria continuam pendentes.

O inventário de produção anterior à migração contém um pedido marcado como pago e uma inscrição ativa, sem `ChargeAttempt` associado. Isso não comprova liquidação pelo Mercado Pago. A migração preserva esse legado e não fabrica uma transação nem altera o estado existente. A organização deve conferir esse registro com o comprovante original.

## Configuração

Na conta própria, crie uma aplicação de Checkout Transparente/Checkout API, habilite os meios desejados e configure a notificação **Pagamentos** no endpoint HTTPS:

`https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/`

Configure no ambiente do servidor, sem inserir credenciais em templates, JavaScript, Git ou documentos:

| Variável | Finalidade |
| --- | --- |
| `MP_ACCESS_TOKEN` | Token privado da aplicação/conta recebedora correta |
| `MP_PUBLIC_KEY` | Chave pública da mesma aplicação, usada pelo CardForm no navegador |
| `MP_WEBHOOK_SECRET` | Assinatura secreta da configuração de Webhooks |
| `MP_COLLECTOR_ID` | ID da conta recebedora, confrontado em cada pagamento |
| `MP_WEBHOOK_URL` | URL HTTPS completa do endpoint acima |
| `MP_ENVIRONMENT` | `sandbox` para homologação; `production` para pagamentos reais |
| `MP_API_BASE` | `https://api.mercadopago.com` — outros hosts são recusados |
| `MP_WEBHOOK_MAX_AGE_SECONDS` | Tolerância do timestamp assinado, padrão 900 segundos |

O campo `live_mode` retornado pelo provedor deve corresponder ao ambiente selecionado. Não confunda credenciais de teste da aplicação com contas de teste: siga o modo recomendado no painel Mercado Pago e confirme o `live_mode` na homologação. O código não flexibiliza essa conferência silenciosamente.

O CardForm exige HTTPS no checkout e acesso do navegador a `https://sdk.mercadopago.com/js/v2`. Número, validade e CVV ficam em campos seguros do SDK. O servidor recebe um token descartável; não recebe PAN nem CVV pelo formulário implementado. O total e a referência são calculados pelo servidor, independentemente do navegador.

## Verificação e implantação

1. Execute `python manage.py migrate` para criar `PaymentTransaction` e a chave única de notificações.
2. Execute `python manage.py check_mercadopago`. Esse comando confere presença/formato e não faz rede nem revela segredos.
3. Execute explicitamente `python manage.py check_mercadopago --verify-account` quando o token próprio estiver configurado. Faz somente `GET /users/me`, exibe o ID retornado e exige que corresponda a `MP_COLLECTOR_ID`. Não cria cobrança.
4. Reinicie o serviço após atualizar o ambiente. Configure o mesmo endpoint e segredo no painel do Mercado Pago.
5. Homologue na conta própria usando os compradores/cartões de teste indicados pelo Mercado Pago. Verifique aprovação, pendência, recusa, assinatura de webhook, repetição de notificação e devolução. As respostas simuladas dos testes automatizados não substituem essa etapa.
6. Ative credenciais reais e `MP_ENVIRONMENT=production` somente após a homologação. Confirme novamente a conta recebedora e a entrega do webhook.

## Regras de confirmação

- O webhook exige HMAC SHA-256 válido, `data.id` da query e `x-request-id`. Sem segredo configurado não há bypass, inclusive em desenvolvimento.
- A notificação apenas aponta o pagamento. O backend consulta `GET /v1/payments/{id}` com autenticação antes de alterar pedido, financeiro ou inscrição.
- Acesso às páginas, cobrança e acompanhamento exige o comprador autenticado ou a sessão do checkout convidado. Conhecer o UUID do pedido, compartilhar e-mail ou ser homônimo não concede acesso. Cada participante gera uma pessoa separada; o vínculo com a conta depende da reivindicação pessoal.
- A conciliação confronta ID, `external_reference`, `metadata.order_id`, `metadata.charge_attempt_id`, recebedor, ambiente, moeda e quantia exata em centavos.
- Apenas `approved`, sem devolução, permite a confirmação comercial. Pendências/autorização não liberam a inscrição. Recusa/cancelamento permitem nova tentativa enquanto a reserva estiver válida.
- Cada tentativa é persistida antes da chamada externa com UUID v4 em `X-Idempotency-Key`. Duplo clique, reenvio e uma resposta ambígua não iniciam uma segunda cobrança. HTTP 5xx, timeout e resposta inesperada ficam pendentes de conciliação.
- A deduplicação distingue notificações de transações. A notificação pendente não impede que uma notificação posterior confirme o mesmo pagamento. Snapshots antigos não regridem uma liquidação.
- Capacidade, lotes, alocações, inscrições, benefícios e lançamentos são conciliados em uma transação. Pagamento tardio, evento cancelado, capacidade insuficiente, inconsistência das linhas ou cobrança duplicada levam a `PAID_NEEDS_REVIEW`; não criam vagas adicionais.
- O checkout reserva tanto vagas gerais quanto vagas do lote, incluindo todos os participantes do grupo, antes de enviar uma cobrança. Reservas vencidas são liberadas antes de novas inscrições, inclusive quando o checkout foi abandonado sem gerar cobrança.
- Pix exige prazo mínimo de 30 minutos na API. O checkout usa pelo menos 31 minutos para cobrir a latência e preserva a reserva até dois minutos depois do vencimento. A imagem/código exibidos vêm da resposta real do provedor.
- A receita é reconhecida uma vez. As taxas vêm de `fee_details`, somente quando debitadas do recebedor; não se aplicam percentuais inventados. Atualizações de taxas/devoluções geram apenas a diferença no livro financeiro.
- Devolução total e chargeback revogam credenciais/benefícios e liberam a capacidade uma vez. Devolução parcial suspende o acesso para a organização definir as inscrições afetadas. Devolver uma cobrança duplicada não revoga os direitos da cobrança original.
- Pagamento não significa aceite legal. QR de credenciamento é emitido apenas quando as versões vigentes dos termos foram aceitas. A ausência de termos ou aceite continua como pendência operacional; isso não impede registrar corretamente o recebimento.

## Recuperação operacional

`python manage.py reconcile_mercadopago --limit 100` consulta tentativas pendentes ou desconhecidas. Não cria cobranças. Quando uma resposta de criação foi perdida, procura pelo `external_reference`, confere o ID da tentativa no metadata e obtém o recurso completo autenticado antes de conciliar. Se a busca ainda não encontrar o pagamento, a tentativa continua bloqueada para evitar duplicação.

`python manage.py reconcile_mercadopago --charge-id UUID` consulta uma tentativa específica, inclusive uma já aprovada, para conferir atualização de taxa, devolução ou contestação. Após ativar a conta, esse comando pode ser executado periodicamente pela operação como recuperação de webhook; não foi criado agendamento automático de cobrança.

`python manage.py expire_checkout_orders --limit 1000` libera reservas vencidas em lote, sem chamada ao Mercado Pago e sem alterar pedidos pagos. Pode ser agendado pela operação para liberar capacidade mesmo quando não há novos acessos ao checkout. Se um pagamento chegar depois da liberação, será registrado e encaminhado à conferência, sem ocupar automaticamente uma vaga já vendida.

Pedidos `PAID_NEEDS_REVIEW`, devoluções parciais e tentativas ambíguas precisam de conferência da organização. Não transforme ausência de resposta em pagamento recusado e não peça outro pagamento antes de consultar a conta. Não há botão de reembolso automático nesta entrega: o sistema acompanha devoluções efetuadas no Mercado Pago e registra o resultado autenticado.

O fluxo usa `select_for_update` e unicidade no banco. PostgreSQL é recomendado para concorrência de produção; SQLite serializa gravações e pode responder com bloqueio sob carga. A suíte cobre efeitos idempotentes e transacionais, mas não substitui ensaio de concorrência em PostgreSQL ou homologação de ponta a ponta da conta.

## Validação local

`python manage.py test tests.test_payments --noinput`

Os testes substituem HTTP por mocks e nunca usam credenciais reais. Cobrem payload/API, indisponibilidade, assinatura, origem, quantia, moeda, conta, ambiente, notificações sucessivas/repetidas, atraso, capacidade, parcelas inválidas, estados, recuperação, estornos, taxa real e conteúdo honesto das páginas.

## Referências utilizadas

- [Mercado Pago — Pix via Payments API](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/integration-configuration/integrate-pix)
- [Mercado Pago — CardForm e tokenização](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/integration-configuration/card/integrate-via-cardform)
- [Mercado Pago — envio de Pix pelo Payment Brick](https://www.mercadopago.com.br/developers/pt/docs/checkout-bricks/payment-brick/payment-submission/pix)
- [SDK oficial — validação de assinatura e timestamp de webhook](https://github.com/mercadopago/sdk-nodejs/blob/master/src/utils/webhook/index.ts)

Do Navie Vibe foram adaptados os padrões de cobrança real por Payments API, conta recebedora explícita, confirmação atômica, recuperação por consulta e taxas obtidas do provedor, observados em `eventos/checkout.py` e `financeiro/mercadopago_sync.py`. Não foram copiados tokens, compradores, dados financeiros ou configuração de outro tenant.
