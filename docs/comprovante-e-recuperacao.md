# Comprovante e recuperação de acesso

O comprovante é gerado na leitura, sem gravar PDFs em mídia pública. A rota `minha_conta:registration_pdf` aceita o titular vinculado, o comprador autenticado, a sessão original de compra ou um operador autorizado para inscrições/credenciamento da edição. Coincidência de e-mail não concede acesso. A resposta é um anexo PDF com `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer` e `nosniff`.

`apps/registrations/eligibility.py` centraliza a consulta de elegibilidade, sem emitir ou restaurar credenciais: pedido PAID, inscrição ACTIVE e READY/CHECKED_IN, todos os documentos publicados/vigentes aceitos pelo titular vinculado e credencial válida. Evento cancelado/arquivado bloqueia a operação. Evento concluído mantém a credencial válida; não foi criada uma data limite de retirada. O scanner usa a mesma regra.

`apps/registrations/pdf.py` usa ReportLab 4.4.9, com fontes Vera distribuídas no próprio pacote. O PDF apresenta os estados reais do pagamento e da inscrição, dados do participante, programação no fuso do evento, benefícios e orientações. Pedido pendente, cancelamento, transferência, revisão e reembolso não são apresentados como inscrição confirmada. O QR contém somente o token atual da credencial elegível e não causa emissão, rotação ou reativação de credencial. É uma fotografia da situação na emissão: o atendimento sempre consulta a situação atual.

As orientações vêm de `apps/registrations/instructions.py`, compartilhadas com `registrations/_pickup_instructions.html`. Elas pedem o comprovante no celular ou impresso, esclarecem pagamento e aceite, saldo de benefícios e caráter pessoal do QR. Não criam exigência documental, prazo ou local que não tenha sido configurado para o evento.

## Recuperação

A confirmação PAID cria uma conta nova para o comprador quando o e-mail ainda não existe. A origem fica em CheckoutAccount e a senha começa inutilizável. O primeiro acesso usa a sessão autorizada do pedido original para definir a senha, com validação e CSRF; nunca recupera conta já existente só pelo e-mail digitado, nem redefine uma senha já ativada. Conta existente exige sua senha habitual para vincular a compra convidada. Essa etapa não depende de SMTP, não vincula automaticamente o titular e não aceita os termos por ele. O acesso administrativo público está no rodapé.

`apps/identity/password_recovery.py` usa tokens de uso único do Django, validação de senha, CSRF e redirecionamento para retirar o token da URL antes do formulário. A definição da senha verifica novamente o token dentro da transação, com bloqueios na ordem pedido → origem da conta → usuário. Duas abas não podem reutilizar o mesmo token para sobrescrever uma senha já alterada.

Contas ativas com senha utilizável podem recuperar acesso. Contas criadas pela compra e ainda sem senha também podem fazê-lo quando o pedido de origem continua PAID e a conta não possui privilégios de equipe. A ativação não cria vínculo de titularidade com uma pessoa nem aceita documentos. Conta com senha utilizável continua podendo consultar histórico após reembolso.

Os links usam `PUBLIC_SITE_URL` configurado, sem confiar no cabeçalho Host. Produção exige backend SMTP e host configurados; backends console/arquivo não são utilizados. Quando falta configuração, a tela informa que nenhum link foi enviado, oferece contato e, se houver pedido PAID na sessão original, retorno ao primeiro acesso da compra. Não há promessa de envio com SMTP ausente. A solicitação com configuração válida retorna resposta genérica, inclusive para conta inexistente; há intervalo de cinco minutos entre envios para o mesmo endereço e falhas de transporte são registradas sem endereço, token ou detalhes privados do servidor.

## Validação

Os testes `test_registration_pdf`, `test_participant_access` e `test_password_recovery` verificam autorização, estado, ausência de mutações na leitura, bloqueios de credencial, conta pendente, pagamento, revogação, expiração, duas abas, CSRF, origem fixa e indisponibilidade de e-mail. A revisão visual usou três PDFs exclusivamente sintéticos: confirmado, pendente e cancelado com textos longos e cinco benefícios. Os três renderizaram em uma página A4 sem cortes. O QR do PDF confirmado foi decodificado a partir do PNG renderizado e coincidiu com o token sintético. Nenhum dado pessoal de produção foi usado nesse QA; nenhum e-mail real foi enviado.
