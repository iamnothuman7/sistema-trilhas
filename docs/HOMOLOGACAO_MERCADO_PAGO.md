# Homologação Mercado Pago — 23/10/2026

## Ambientes

Produção: `https://equipemorcego.nabio.pro`, diretório `/var/www/apps/equipemorcego`, serviço `equipemorcego.service`. A configuração de pagamentos de produção continua em rascunho, sem ativação de cobranças reais.

Homologação: `https://homologacao.equipemorcego.nabio.pro`, diretório `/var/www/apps/equipemorcego-homologacao`, serviço `equipemorcego-homologacao.service`, usuário Unix `morcego-hml`, porta local 8056. Banco SQLite, mídia, sessões, CSRF e chaves Django são exclusivos desse ambiente. O envio de e-mail usa backend em memória. O serviço está habilitado para iniciar automaticamente após reboot.

O endpoint HTTPS `/integracoes/mercadopago/webhook/` aceita as notificações externas com a validação de assinatura da aplicação. Desde 24/10/2026, as demais rotas também permitem teste manual pelo titular mediante autenticação HTTP Basic exclusiva da homologação; sem autenticação retornam 401. Conexões locais no servidor continuam permitidas. A senha não pertence ao administrador do sistema nem à conta Mercado Pago; seu hash fica em arquivo restrito do Nginx, fora do repositório. O certificado usa a renovação existente do Certbot. Nginx envia `X-Robots-Tag: noindex, nofollow, noarchive`.

O perfil `config.settings.homologation` exige o diretório dedicado e `MP_ENVIRONMENT=sandbox`. Não carregue esse perfil na instalação de produção. O ambiente de teste compartilha apenas as dependências Python da instalação existente; não compartilha o banco, `.env` ou arquivos de participantes. O serviço tem `ReadOnlyPaths` apontando para a instalação produtiva.

## Aplicação e configuração conferidas

A aplicação principal `8997325272050358` (`trilhas-teste`, recebedor `1204420711`) estava configurada como Checkout Pro. No painel do Mercado Pago foi alterada para **Checkout Transparente → API Pagamentos**. O par de credenciais **de teste** dessa aplicação foi instalado somente na homologação. A autenticação do titular foi concluída pelo próprio titular.

As configurações de Webhooks foram salvas e relidas: URL de teste `https://homologacao.equipemorcego.nabio.pro/integracoes/mercadopago/webhook/`, URL de produção `https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/` e evento **Pagamentos (legacy)**, correspondente ao tópico `payment`. A assinatura existente foi preservada. Cadastrar a URL produtiva não ativa o recebimento de dinheiro no sistema.

O teste inicial com a aplicação filha `4047579793568288` foi arquivado separadamente. Antes da troca para a aplicação principal, foi criado outro banco vazio: histórico de cobrança e identidades de conexão não foram misturados.

## Resultados observados na configuração final

| Verificação | Resultado |
| --- | --- |
| Suítes `test_payments`, `test_payment_connection`, `test_payment_recovery` | 75 testes locais passaram; HTTP externo substituído por mocks |
| Tokenização com a Public Key de teste | HTTP 201, token com `live_mode=false` |
| Mastercard APRO | HTTP 201, `approved/accredited`, `live_mode=false` |
| Mastercard FUND | HTTP 201, `rejected/cc_rejected_insufficient_amount`, `live_mode=false` |
| Mastercard CONT | HTTP 201, `in_process/pending_contingency`, `live_mode=false` |
| Pix | HTTP 201, `pending`, QR do sandbox, `live_mode=false` |
| Cartão pelo formulário HTTP do sistema | CSRF válido, redirecionamento, tentativa SUCCEEDED e pedido PAID |
| Pix pelo formulário HTTP do sistema | CSRF válido, redirecionamento, tentativa PENDING e pedido AWAITING_PAYMENT |
| Reenvio do formulário | Nenhuma tentativa ou cobrança adicional |
| Simulador oficial do Mercado Pago | Notificações de Pix e cartão entregues com HTTP 200 OK; assinatura válida e evento processado no banco |
| Aprovação via webhook | Consulta autenticada do recurso, inscrição ACTIVE, capacidade comprometida e receita única de R$ 120 |
| Conta do comprador | Criada após aprovação; senha inutilizável até definição pelo comprador |
| Notificação repetida | HTTP 200, sem duplicação de receita ou ocupação de vaga |
| Inscrição paga sem aceite | NOT_READY, sem credencial de acesso |
| Aceite fictício no banco de QA | Credencial emitida após vínculo pessoal e aceite do documento fictício |
| Reembolso parcial de R$ 40 | API HTTP 201; PARTIALLY_REFUNDED, QR revogado e estorno contabilizado |
| Reembolso dos R$ 80 restantes | API HTTP 201; REFUNDED, total R$ 120, capacidade liberada e benefícios revogados uma vez |
| Pix pendente no webhook | Sem reconhecimento de receita |
| Cancelamento do Pix | API HTTP 200; tentativa CANCELED |
| Acesso anônimo ao pedido | HTTP 404 |
| POST de pagamento sem CSRF | HTTP 403, sem criação de tentativa |
| Segredos no HTML | Access Token e segredo do webhook ausentes |

Foram verificados 12 cenários HTTP do webhook: assinatura ausente/incorreta/vencida/futura, identificador ausente, JSON inválido, divergência entre corpo e query, tópico ignorado com assinatura válida (timestamp em segundos e milissegundos), método HTTP inválido, recurso inexistente e repetição desse recurso. Falhas não confirmam inscrições nem criam receita; a notificação não conciliada permanece disponível para nova tentativa.

Evidências de sandbox: cartão APRO `1354121803`, FUND `1354130675`, CONT `1354121807`, Pix `1354130663`; cartão aprovado pelo checkout `1354121913` e Pix pelo checkout `1354130769`. O APRO inicial foi integralmente reembolsado e o Pix inicial cancelado. O simulador oficial entregou notificações dos recursos `1354130663`, `1354121803` e `1354121913` com resposta 200. O último também foi confirmado no banco como assinado/processado, sem duplicar a receita.

## Limites e ocorrências registradas

- Três solicitações iniciais pelo checkout retornaram HTTP 500 do Mercado Pago. A consulta posterior não encontrou pagamentos associados. Permanecem UNKNOWN e não foram reenviadas com outra chave; a proteção contra cobrança duplicada foi mantida. Depois, novos pedidos fictícios de cartão e Pix passaram pelo mesmo checkout. Não foi determinada a causa transitória do provedor e não foi necessário flexibilizar validações ou alterar o gateway.
- As notificações do simulador foram enviadas pelo próprio Mercado Pago. Os testes adicionais de repetição, estorno e entradas inválidas foram assinados pelo executor de QA, sempre conciliando recursos consultados na API autenticada. Não confundir esses testes com evidência de entrega automática de todos os eventos de produção.
- O teste do checkout exercitou tokenização com Public Key, sessão autorizada, CSRF, formulário HTTP, consulta de estado e confirmação. Não equivale a um teste visual completo dos campos seguros do SDK em cada navegador/dispositivo.
- A homologação não realizou cobranças reais. As contagens produtivas foram preservadas: zero tentativas Mercado Pago, zero transações Mercado Pago, um pedido legado, uma inscrição e um lançamento financeiro.
- O rascunho antigo de produção não deve ser ativado: ele ainda precisa receber as credenciais reais correspondentes à conta própria da Equipe Morcego. A homologação não ativou esse rascunho nem copiou credenciais de teste para a configuração produtiva em uso.

## Ativação futura da conta real

1. Na aplicação da conta própria da Equipe Morcego, confirmar **Checkout Transparente → API Pagamentos** e obter o par de credenciais de **produção**. Se for outra aplicação, configurar também seu webhook e segredo e repetir a homologação nessa identidade antes de ativá-la.
2. Em **Gestão → Mercado Pago**, substituir os dados antigos do rascunho: Public Key, Access Token, segredo do webhook e ID recebedor da conta correta; selecionar ambiente `production`. Não usar as credenciais TEST da homologação.
3. Conferir a URL produtiva `https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/` e evento **Pagamentos (legacy)** nessa mesma aplicação.
4. Clicar **Salvar rascunho** e **Testar conta sem cobrar**. Conferir o recebedor. Esse teste faz apenas `/users/me`; não comprova cobrança nem entrega de webhook.
5. Clicar **Ativar configuração conferida** dentro de 30 minutos da verificação, confirmando conta, ambiente, webhook e homologação. Nenhuma dessas ações foi feita para produção durante estes testes.
6. Com autorização da organização, acompanhar a primeira compra real e a notificação automática, verificando pedido, inscrição, financeiro e comprovante. Configurar execução periódica de `reconcile_mercadopago` para recuperação das pendências. O comando apenas consulta; não cria cobranças.

## Operação da homologação

As evidências sanitizadas ficam em arquivos `homologation-*-results.json` no diretório isolado, com modo 600. O `.env` pertence a `morcego-hml:morcego-hml` com modo 600; manter esse proprietário inclusive quando uma ferramenta substituir o arquivo atomicamente. Nunca publicar esses arquivos, o banco ou segredos no Git.

Após reiniciar o serviço, verificar `systemctl is-active equipemorcego-homologacao.service` e o webhook sem assinatura (HTTP 401). O diretório público deve continuar exigindo autenticação externamente (401 sem credenciais), e o webhook sem assinatura deve retornar 401 independentemente dessa autenticação. Para conciliação manual, executar o comando de gestão a partir do diretório isolado com `--settings=config.settings.homologation`; nunca executar os cenários fictícios usando `config.settings.production`.

## Referências oficiais

- [Compra teste via Payments](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/integration-test/make-test-purchase)
- [Cartões e cenários de teste](https://www.mercadopago.com.br/developers/pt/docs/checkout-api-payments/additional-content/your-integrations/test/cards)
- [Webhooks: ambiente, tópico e assinatura](https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks)
- [Erros da criação de pagamentos](https://www.mercadopago.com.br/developers/pt/reference/online-payments/checkout-api-payments/create-payment/post)

## Teste manual pelo titular — 24/10/2026

Abra `https://homologacao.equipemorcego.nabio.pro/checkout/inscricao/homologacao-pagamento/` e informe o usuário e a senha de acesso à homologação entregues separadamente. Escolha a inscrição fictícia de R$ 120 e preencha os dados de um participante fictício. Use um e-mail diferente do cadastrado na conta vendedora do Mercado Pago.

Para aprovação: Mastercard `5480 8328 0103 3311`, validade `11/30`, CVV `123`, titular `APRO`, CPF `12345678909`, uma parcela. Para conferir outras respostas, inicie um novo pedido fictício e use titular `FUND` (recusado por saldo insuficiente) ou `CONT` (em análise). Não reenvie pedidos cujo resultado esteja inconclusivo.

O aviso de ambiente de testes aparece nas páginas públicas da homologação. Pix permite conferir geração de QR e acompanhamento de pendência; não faça transferência bancária real. O ambiente não envia e-mails externos. A homologação não libera uma inscrição no evento real.

A conta e as credenciais produtivas continuam sem ativação. Os testes de formulário HTTP e API não devem ser descritos como uma validação visual de todos os navegadores ou como garantia de indisponibilidade zero do Mercado Pago.
