# Homologação Mercado Pago — 23/10/2026

## Ambientes

Produção: `https://equipemorcego.nabio.pro`, diretório `/var/www/apps/equipemorcego`, serviço `equipemorcego.service`. A configuração de pagamentos de produção continua em rascunho, sem ativação de cobranças reais.

Homologação: `https://homologacao.equipemorcego.nabio.pro`, diretório `/var/www/apps/equipemorcego-homologacao`, serviço `equipemorcego-homologacao.service`, usuário Unix `morcego-hml`, porta local 8056. Banco SQLite, mídia, sessões, CSRF e chaves Django são exclusivos desse ambiente. O envio de e-mail usa backend em memória. O serviço não está habilitado para iniciar automaticamente após reboot.

Somente o endpoint HTTPS `/integracoes/mercadopago/webhook/` aceita acesso externo. As demais rotas retornam 403 para acessos externos e são testadas por conexão local no servidor, com TLS válido. O certificado usa a renovação existente do Certbot. Nginx envia `X-Robots-Tag: noindex, nofollow, noarchive`.

O perfil `config.settings.homologation` exige o diretório dedicado e `MP_ENVIRONMENT=sandbox`. Não carregue esse perfil na instalação de produção. O ambiente de teste compartilha apenas as dependências Python da instalação existente; não compartilha o banco, `.env` ou arquivos de participantes. O serviço tem `ReadOnlyPaths` apontando para a instalação produtiva.

## Resultados observados

| Verificação | Resultado |
| --- | --- |
| Suítes `test_payments`, `test_payment_connection`, `test_payment_recovery` | 75 testes locais passaram; HTTP externo substituído por mocks |
| Tokenização por credencial de teste autenticada | HTTP 201 |
| Cartão Visa, cenário APRO | HTTP 201, `approved`, `live_mode=false` |
| Cartão Mastercard, cenário FUND | HTTP 201, `rejected/cc_rejected_insufficient_amount`, `live_mode=false` |
| Pix | HTTP 201, `pending`, QR real do sandbox, `live_mode=false` |
| Pix pelo formulário HTTP do sistema | POST com CSRF, redirecionamento correto, cobrança pendente; reenvio não cria nova tentativa |
| Aprovação recebida pelo endpoint de webhook | Consulta autenticada do pagamento, pedido PAID, inscrição ACTIVE, capacidade comprometida e receita única de R$ 120 |
| Conta do comprador | Criada automaticamente após a aprovação; senha inutilizável até definição pelo comprador |
| Repetição da mesma notificação | HTTP 200 `already_processed`; sem duplicação financeira |
| Nova notificação sobre o mesmo pagamento | HTTP 200 `processed`; sem nova receita ou ocupação de vaga |
| Inscrição paga sem aceite | Permanece operacionalmente NOT_READY, sem credencial de acesso |
| Aceite fictício no banco de QA | Credencial emitida apenas após o vínculo pessoal e aceite do documento fictício |
| Reembolso parcial de R$ 40 | API HTTP 201; pedido PARTIALLY_REFUNDED, QR revogado e estorno contabilizado |
| Reembolso dos R$ 80 restantes | API HTTP 201; pedido REFUNDED, R$ 120 devolvidos e sem duplicar lançamentos ao repetir notificação |
| Pix pendente no webhook | QR preservado, sem reconhecimento de receita |
| Cancelamento do Pix | API HTTP 200; tentativa CANCELED após conciliação |
| Acesso anônimo ao pedido | HTTP 404 |
| POST de pagamento sem CSRF | HTTP 403, sem criação de tentativa |
| Segredos no HTML do checkout | Access Token e segredo do webhook ausentes |

Foram verificados 12 cenários HTTP do webhook: assinatura ausente/incorreta/vencida/futura, identificador ausente, JSON inválido, divergência entre corpo e query, tópico ignorado com assinatura válida (timestamp em segundos e milissegundos), método HTTP inválido, recurso inexistente e repetição desse recurso. Falhas não confirmam inscrições nem criam receita; a notificação não conciliada permanece disponível para nova tentativa.

**Origem das notificações de QA:** as requisições de teste foram assinadas pelo executor com a chave configurada. O endpoint consultou recursos de pagamento verdadeiros do sandbox via API autenticada. Esses testes comprovam o processamento HTTP e a conciliação, mas não substituem a entrega assinada pelo Mercado Pago.

## Ocorrências e pendências

- O Access Token inicialmente fornecido retornou HTTP 401 `Unauthorized use of live credentials` na criação de pagamentos. O simples sucesso em `/users/me` não comprova permissão para cobrar.
- A aplicação de teste `4047579793568288` expôs uma credencial de teste adicional. Ela foi validada para o mesmo recebedor de teste e usada somente na homologação. Com comprador de teste, permitiu aprovação, recusa, Pix e estornos.
- Uma tentativa APRO com Mastercard retornou HTTP 500. Foi mantida UNKNOWN; a busca autenticada não encontrou o pagamento. Não foi reenviada como uma nova cobrança do mesmo pedido.
- O cenário CONT retornou token de cartão com `live_mode=true` na verificação posterior; a nova tentativa foi interrompida antes da criação do pagamento. A pendência comercial foi exercitada com Pix, mas a pendência específica de cartão ainda precisa de nova validação.
- A Public Key originalmente fornecida retornou token APRO com `live_mode=true`, incompatível com a credencial de servidor usada no sandbox. Essa tentativa não foi enviada para pagamento. O par de credenciais do frontend e backend precisa ser conferido no painel.
- Ainda falta comprovar o envio de uma notificação pelo Mercado Pago com o segredo e a URL da aplicação correta. A tela de credenciais exige revalidação de identidade pelo titular.

Não declarar a integração pronta para pagamentos reais enquanto essas pendências estiverem abertas. Nenhum pagamento de produção foi criado por esta homologação.

## Finalização da configuração

1. Na aplicação correta, conferir Checkout Transparente via Payments e obter Public Key e Access Token correspondentes ao mesmo ambiente de teste. A documentação recomenda as credenciais de teste do usuário produtivo para este fluxo. Não deduzir ambiente somente pelo prefixo da chave.
2. Em Webhooks, configurar **URL modo teste**: `https://homologacao.equipemorcego.nabio.pro/integracoes/mercadopago/webhook/`, com evento **Pagamentos** (`payment`). Conferir a assinatura secreta dessa aplicação com o ambiente de homologação; não renovar a chave de outra integração.
3. Executar o checkout completo com cartão e Pix. Conferir `live_mode=false`, recebedor, valor, referência, metadata e assinatura da notificação enviada pelo provedor. Guardar IDs e respostas sanitizadas, sem tokens ou dados de cartão.
4. Depois de concluir a homologação, configurar o rascunho de produção com a conta própria da Equipe Morcego, ambiente `production`, o par de credenciais reais e o segredo correspondente.
5. A URL produtiva é `https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/`. Fazer **Testar conta sem cobrar**, conferir a conta recebedora e só então **Ativar configuração conferida**. A declaração de homologação só deve ser marcada quando os testes pendentes estiverem concluídos.
6. Conferir a primeira operação real com a organização, verificando pagamento, pedido, inscrição, financeiro e comprovante. O código consulta o provedor; não aprova pagamentos pelo conteúdo do webhook nem por mudança manual do retorno.

## Operação da homologação

As evidências sanitizadas ficam em arquivos `homologation-*-results.json` no diretório isolado, com modo 600. O `.env` pertence a `morcego-hml:morcego-hml` com modo 600; manter esse proprietário inclusive quando uma ferramenta substituir o arquivo atomicamente. Nunca publicar esses arquivos, o banco ou segredos no Git.

Após reiniciar o serviço, verificar `systemctl is-active equipemorcego-homologacao.service` e o webhook sem assinatura (HTTP 401). O diretório público deve continuar retornando 403 externamente. Para conciliação manual, executar o comando de gestão a partir do diretório isolado com `--settings=config.settings.homologation`; nunca executar os cenários fictícios usando `config.settings.production`.

## Referências oficiais

- [Compra teste via Payments](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/integration-test/make-test-purchase)
- [Cartões e cenários de teste](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/additional-content/your-integrations/test/cards)
- [Webhooks: ambiente, tópico e assinatura](https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks)
- [Erros da criação de pagamentos](https://www.mercadopago.com.br/developers/pt/reference/online-payments/checkout-api-payments/create-payment/post)
