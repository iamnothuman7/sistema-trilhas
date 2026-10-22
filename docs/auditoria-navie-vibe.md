# Auditoria do Naviê Vibe e adaptação ao Sistema Trilhas

Data: 22/10/2026. Fonte principal: clone local de `ramilos-comunicacoes/navie-vibe`, commit `3a1125960a3ac259ddf111718b70886f251aea4b`, em `references/navie-vibe/`. As referências de arquivo e linha abaixo usam essa raiz, salvo indicação explícita de Sistema Trilhas.

O Naviê possui padrões úteis de autorização por empresa, isolamento de contexto entre abas, transações, idempotência, checkout e auditoria. Esses padrões foram selecionados e adaptados ao domínio de edições de eventos, inscrições individuais e valores inteiros em centavos do Trilhas. A existência de código no Naviê não é evidência de que todos os módulos tenham a mesma maturidade: há implementações recentes mais rigorosas e caminhos antigos com permissões permissivas, métricas demonstrativas e riscos de concorrência.

## Escopo e limites

- Inventário da referência Git: 1.247 arquivos versionados; 744 Python, 295 HTML, 53 JavaScript e 36 CSS. Foram examinados arquitetura, modelos centrais, políticas, controles de acesso, checkout de eventos, financeiro, estoque, relatórios e testes relevantes.
- Análise estática. Não foram utilizados credenciais, arquivos `.env`, banco, uploads, dados pessoais ou mídias do Naviê; não foram executados scripts operacionais, de diagnóstico, carga, manutenção ou implantação.
- A amostra de código em `references/navie-vibe-server/` foi comparada em arquivos selecionados. Ela difere do GitHub em `hoteis/middleware.py`, `financeiro/views/webhook.py`, `estoque/views.py`, `administracao/views.py` e `requirements.txt`. A amostra não contém `eventos/access.py`, `eventos/checkout.py`, `hoteis/hotel_access.py`, `financeiro/oauth_state.py` nem `financeiro/attachments.py`. Isso indica divergência entre as referências disponíveis; não permite concluir, isoladamente, quais rotas ou versões estão efetivamente em execução no servidor.
- Não se presume disponibilidade de integrações externas, emissão fiscal, entregabilidade de mensagens, segurança de infraestrutura ou integridade dos dados apenas pela leitura do código.
- A referência está ignorada pelo Git do Trilhas. O código adaptado, os testes e este relatório são os artefatos destinados ao projeto.

## Arquitetura efetiva

O Naviê é um monólito Django modular com várias verticais: hotelaria, eventos, cinema, restaurantes, parques, comércio, financeiro, estoque e identidade compartilhada. A árvore de URLs em `navievibe/urls.py:27–42` distribui as áreas públicas, de gestão e APIs. Copiar a árvore integral traria entidades, dependências e fluxos sem relação com os eventos da Equipe Morcego.

`core/models.py:126–166` define o vínculo central `EmpresaUsuario`, com empresa, usuário, vertical, papel e atividade; a unicidade em `:153` impede duplicação do mesmo vínculo. Esse é o principal conceito reutilizável: capacidade explícita associada a um escopo, validada também no backend.

A configuração ativa em `navievibe/settings.py:160–164` aponta os aliases de banco das verticais para o mesmo banco padrão; o router ativo é o de analytics. O arquivo legado `core/routers.py` não deve ser tratado como a arquitetura ativa de isolamento físico de bancos. A segregação de dados depende principalmente dos vínculos e dos filtros de empresa/recurso.

O Naviê usa diretamente o usuário Django padrão em vários módulos. O Trilhas possui `identity.User` próprio, com identificador UUID, e vínculos `EventMembership`. Não se deve importar migrações ou relações de usuário do Naviê. O mesmo vale para suas versões de dependências: a referência declara Django 6.0.4; o Trilhas utiliza Django 5.2 e Python 3.11.

## Regras que valem reaproveitar

| Área | Evidência no Naviê | Adaptação para Trilhas |
| --- | --- | --- |
| Escopo autorizado | `eventos/access.py:11–34` filtra produtoras autorizadas e só aceita seleção de sessão dentro desse conjunto | Resolver edição autorizada antes de consultar ou alterar seus registros |
| Capacidade por função | `eventos/access.py:47–60` separa gestão, vendas, equipe e financeiro | Financeiro/relatórios só para papéis financeiros ou gerenciais; operação de check-in não implica acesso financeiro |
| Objeto dentro do escopo | `hoteis/hotel_access.py:105–122` resolve reserva somente no hotel autorizado | Buscar despesa, pedido e inscrição junto do `event_id` autorizado, sem confiar apenas em IDs recebidos |
| Contexto por aba | `hoteis/hotel_access.py:24–102` e `hoteis/middleware.py:35–45,78–82` fixam o contexto autorizado no request | Manter evento explícito nas URLs de gestão e verificar o escopo no POST |
| Transação e idempotência | `eventos/checkout.py:343–518`, especialmente `:372–391`; `financeiro/models.py:147–154` | UUID único por envio de despesa, hash do conteúdo e criação atômica de despesa, razão e auditoria |
| Concorrência | `eventos/checkout.py:349,425,461` usa locks para evento, lote e cupom; `:923–976` revalida confirmação | Travar despesa durante baixa/cancelamento; repetir a baixa não duplica saída financeira |
| Estoque comercial reservado | `eventos/checkout.py:343–518` considera vendas pendentes e confirmadas na capacidade | Preservar o controle de reservas e confirmações de vagas existente no Trilhas; nunca confundir vaga com estoque de camisetas |
| Pagamento confirmado | `eventos/checkout.py:923–976` cria ingressos idempotentes e agenda notificações após commit | Confirmar inscrições e benefícios uma vez, após validação confiável do pagamento |
| Conflito de capacidade | `eventos/checkout.py:1328–1361` mantém estado de conflito antes da tentativa de devolução | Pagamento aprovado não autoriza exceder a capacidade; manter ocorrência recuperável e processo explícito de reembolso |
| Cancelamento | `eventos/checkout.py:1364–1391` trava a venda e devolve estoque uma vez | Cancelar compromisso pendente com motivo; despesa paga exige ajuste preservando o histórico |
| Check-in | `eventos/operation_views.py:330–368` valida papel, produtora, ingresso e situação sob transação | Validar evento e credencial, impedir uso duplicado e registrar operador/data |
| OAuth | `financeiro/oauth_state.py:12–41` associa estado assinado a usuário/empresa/recurso, expira em 600 s e consome uma vez | Aplicável a futura vinculação de conta de pagamento; não copiar tokens ou credenciais |
| Arquivos privados | `financeiro/attachments.py:16–42,68–108` valida tamanho, extensão/conteúdo e escopo no download | Base para comprovantes privados futuros; manter limite, autorização e `private, no-store` |
| Auditoria | `core/models.py:340–358` associa ator, recurso, escopo e detalhes | Registrar criação, baixa, cancelamento com motivo e exportação sem copiar conteúdo pessoal para o log |
| Mensagens recuperáveis | `core/models.py:218–247` possui evento de outbox com tentativas e erro | Padrão útil para comunicações após commit; a presença do modelo não comprova uso em todos os fluxos |

A política em `docs/hotel-notifications-policy.md` também traz regras reutilizáveis: todos os acessos a tarefas, anexos, busca e notificações compartilham o mesmo filtro de autorização; notificações são por usuário; não há notificação desnecessária ao próprio autor; ações de equipe não removem o último proprietário nem concedem poderes superiores aos do operador. São boas bases para uma futura central de atividades do Trilhas, sem importar a entidade hotel.

## Estilização e experiência

`docs/design_philosophy.md` define esforço cognitivo mínimo, linguagem direta, redução de campos, cores vinculadas à marca e separação entre a interface pública e o painel interno. O documento de filosofia menciona Poppins/Quicksand e cards arredondados; a definição final do painel interno deve considerar também os templates e estilos B2B efetivamente usados, estudados separadamente na adaptação visual.

Para o Trilhas, as consequências práticas são: uma ação principal por seção, campos com rótulos e mensagens úteis, listas legíveis, estados vazios, navegação consistente por edição e ícones de biblioteca em vez de emojis nativos. Login e painel devem usar a identidade da Equipe Morcego; a marca enviada pelo usuário deve permanecer como arte original, sem moldura ou fundo artificial.

Os relatórios implementados mostram números do banco, o significado dos filtros e tabelas reais. Pedidos e inscrições são filtrados por criação e mostram seu estado atual; o financeiro é filtrado por reconhecimento, e compromissos pendentes por vencimento. Isso evita apresentar um estado atual como se fosse uma fotografia histórica do fechamento de uma data.

## Achados verificáveis que não devem ser copiados

### P1 — Webhook financeiro antigo aceita configuração insuficiente

Em `financeiro/views/webhook.py:62–76`, o segredo de assinatura possui fallback para o access token; a ausência de segredo apenas gera log, e a assinatura inválida só é recusada fora de DEBUG. O caminho ainda consulta o pagamento no provedor, portanto o achado **não significa que um corpo arbitrário determine sozinho o estado pago**. O problema é aceitar notificações sem autenticação confiável quando a configuração é incompleta ou de desenvolvimento.

O mesmo arquivo imprime o payload em `:84` e retorna sucesso HTTP mesmo após exceções capturadas em `:147–150`, sem evidência local de uma fila durável de repetição. Isso pode expor dados em logs e perder uma oportunidade de nova entrega do provedor.

Recomendação: segredo próprio obrigatório, validação independente de DEBUG, consulta autenticada ao provedor, verificação de vínculo/valor/moeda, evento persistido com estado de processamento e repetição segura. A implementação de eventos em `eventos/public_views.py:601–604` já recusa segredo ausente ou assinatura inválida; os dois caminhos não têm a mesma proteção.

### P1 — Recebimento de compra sem lock pode contabilizar estoque duas vezes

`estoque/views.py:551–561` busca a compra e verifica a situação antes de entrar na transação. Não há `select_for_update` nesse caminho. Duas requisições simultâneas podem observar a mesma compra ainda não recebida e incrementar estoque/movimentações duas vezes. A média de custo em `:571–575` também depende de valores lidos e escritos em sequência.

Recomendação: travar compra, itens e produtos antes de validar a transição; garantir unicidade do movimento de recebimento e testar concorrência no mesmo banco usado na produção. O fluxo do Naviê não foi copiado para a baixa de despesas do Trilhas.

### P1 — Saída manual de estoque permite saldo negativo e disputa de atualização

`estoque/views.py:503–543` consome lotes e reduz o estoque global; em `:530–532` a subtração não comprova disponibilidade suficiente. O endpoint em `:691–697` valida quantidade positiva e produto do hotel, mas não estabelece lock da linha antes da operação. Há risco de saldo negativo e de perda de atualização simultânea. O filtro de hotel existe; o problema aqui é consistência, não ausência total de escopo.

A rotina é chamada de PEPS, porém ordena por validade e depois compra em `:511–515`. Na prática, privilegia vencimento, aproximando-se de FEFO. A regra precisa ser nomeada de acordo com seu comportamento antes de reaproveitar.

### P2 — Permissão padrão de hotelaria é permissiva

`hoteis/permissions.py:7–14` avalia perfil ativo, proprietário e algumas regras explícitas, mas termina com `return True` para os demais casos. O comentário registra compatibilidade com comportamento legado. A aplicação também possui middleware de verificação de módulos (`hoteis/middleware.py:91–103`), portanto não é correto afirmar que não existe autorização.

O risco aparece ao criar uma capacidade nova ou pressupor que toda função não listada está negada. No Trilhas, capacidades desconhecidas devem ser recusadas e operações financeiras precisam de papel explícito.

### P2 — Redirecionamento de login admite destino externo

`administracao/views.py:21–53`, especialmente `:42`, aceita destinos `next` contendo dois-pontos ou iniciados por barra. Isso abrange URLs externas e URLs iniciadas por `//`. `eventos/views.py:74` também redireciona diretamente para o valor recebido em POST.

Recomendação: validar host e esquema com a função apropriada do Django e usar um destino interno padrão. O resultado está condicionado ao usuário autenticar no fluxo afetado; não representa leitura arbitrária de dados.

### P2 — Painel administrativo mistura métricas reais com simulação

`administracao/views.py:78–108` inclui `random.random()`, receita fixa e séries fixas junto de contagens do sistema. Esses valores podem servir a demonstração visual, mas não a um painel gerencial publicado. Nenhuma série simulada foi reutilizada nos novos relatórios do Trilhas.

### P2 — Anotações de imutabilidade não são proteção de banco

O modelo de auditoria em `core/models.py:340–358` captura evidências úteis, mas sua declaração por si só não impede update/delete por outras rotinas ou por administrador do banco. O mesmo cuidado vale para o razão do Trilhas: serviços preservam os lançamentos, mas a expressão “imutável” no docstring não equivale a um controle de armazenamento imutável.

Recomendação: manter escrita concentrada em serviços, restringir administração, registrar ajustes em lançamentos separados e considerar controles no banco conforme a necessidade real.

## Testes existentes no Naviê

A inspeção por AST encontrou 807 métodos de teste em 40 arquivos de aplicações. Isso é inventário de código, não resultado de execução nem percentual de cobertura. Foram excluídos da contagem scripts de diagnóstico na raiz, migrações e ferramentas operacionais.

Áreas relevantes: eventos possui 76 testes em `eventos/tests.py` e 23 em `eventos/test_integridade_checkout.py`; estoque possui 9 testes; financeiro possui testes gerais, de anexos, fiscal, concorrência, produtos e configuração. Hotelaria possui conjuntos específicos de isolamento de abas, integridade de reservas, permissões, atividades, notificações, categorias de relatório e desempenho do dashboard.

Casos especialmente úteis em `eventos/test_integridade_checkout.py`: repetição idempotente (`:147`), capacidade comprometida por pedidos pendentes (`:185`), rejeição de webhook (`:290`), conflito de capacidade (`:321`), fila de admissão (`:456`), repetição de reembolso (`:642`) e IP de proxy confiável (`:745`). `eventos/protecao_checkout.py:66–146` implementa tokens com validade, vínculo com sessão/dispositivo e limites de admissão sob transação.

`financeiro/tests_fiscal_concorrencia.py:15` depende de PostgreSQL; `:41–50` testa geração simultânea de números e idempotência fiscal. Esse é um exemplo útil de teste de concorrência real, não substituível por um teste sequencial em SQLite.

Os testes de estoque examinados cobrem recebimento, custo médio e consumo sequencial; não foi encontrado um teste que elimine os riscos concorrentes descritos acima. O checklist `EVENTOS_RELEASE_CHECKLIST.md` registra validação histórica dirigida e ressalva que uma descoberta indiscriminada de testes na raiz importa scripts com efeitos externos. Por isso não foi executado `test` genérico no Naviê nem se reproduziu seu checklist como se fosse resultado desta auditoria.

## Implementação concreta no Sistema Trilhas

| Entrega | Arquivos do Trilhas | Comportamento |
| --- | --- | --- |
| Despesa validada | `apps/finance/forms.py` | Valor convertido com Decimal para centavos, categorias/situações controladas, datas válidas e fornecedor limitado à organização do evento |
| Ciclo da despesa | `apps/finance/services.py` | Criação atômica; identificador único e hash; compromisso a pagar sem saída de caixa; baixa uma vez; cancelamento de pendente com justificativa; auditoria na mesma transação |
| Persistência | `apps/finance/migrations/0003_expense_submission_and_status.py` | Campos opcionais de idempotência para preservar registros antigos e escolhas explícitas de situação |
| Resultado financeiro | `apps/reporting/selectors.py` | Receitas menos estornos, tarifas e despesas do razão; transferências excluídas do resultado; pendências separadas; diferença de conciliação identificada |
| Filtros de período | `apps/reporting/forms.py` e seletores | Intervalo inclusivo em datas locais; início não pode superar fim; datas inválidas não são ignoradas |
| Relatório por edição | `apps/reporting/views.py`, `frontend/templates/gestao/reporting/overview.html` | Indicadores de inscrições/pedidos, categorias, tamanhos informados e movimento financeiro diário; acesso financeiro obrigatório |
| CSV | `apps/reporting/exports.py` | Participantes, pedidos, extrato e resumo com escopo e período; UTF-8 com BOM, separador `;`, valores monetários numéricos precisos, proteção de fórmulas em texto e cache privado |
| Integração de gestão | Funções financeiras de `apps/core/views_gestao.py`, rotas em `apps/core/urls_gestao.py` | Formulário com erros preservados, paginação de despesas e extrato, ações POST, links para relatório |
| Autorização gerencial | `apps/core/permissions.py`, `apps/core/views_gestao.py` | Capacidades explícitas por evento; operações sem mapeamento negadas; duplicar edição exige poder de criar eventos; interface reflete a mesma capacidade |
| Operação consistente | `apps/core/views_gestao.py`, `frontend/static/js/scanner.js` | Mudança de situação com lock e auditoria atômica; entrega identificada por UUID da intenção, mantido em repetição de rede; lock da inscrição antes do benefício |
| Área pessoal | `apps/registrations/views_minha_conta.py` | Visibilidade por comprador do pedido ou vínculo de titular, nunca por coincidência de e-mail; detalhe e QR resolvidos no mesmo escopo |
| Titularidade compartilhável | `apps/registrations/views_minha_conta.py`, templates `claim_link.html` e `claim_confirm.html` | Comprador autenticado ou sessão de compra autorizada gera link por POST; token armazenado como hash, validade de sete dias, consumo único por POST e confirmação pessoal; vínculo existente não é substituído |
| Aceite individual | `apps/registrations/views_minha_conta.py`, serviços de pagamentos compartilhados | Somente titular vinculado aceita todos os documentos vigentes; evidências e auditoria atômicas; pagamento e aceite permanecem condições distintas para liberar QR |
| Validação financeira | `tests/test_reporting_finance.py` | 23 testes de precisão, isolamento, autorização, filtros, idempotência, rollback, ciclo de despesas, cálculo real, exportações, renderização e paginação sem repetição de registros |
| Validação de acesso e operação | `tests/test_management_access.py`, `tests/scanner_ui.test.cjs` | 16 testes de gestão e 7 testes de interface do scanner, incluindo cancelamento, rollback, poderes de criação e repetição de entrega |
| Validação da área pessoal | `tests/test_participant_access.py` | 17 testes de escopo, aceite completo e atual, separação de pagamento, QR, rollback, CSRF, compartilhamento autorizado, uso único e privacidade das respostas |

Rotas: `/gestao/eventos/<event_id>/relatorios/` (`gestao:reports`), financeiro já existente, e ações de pagar/cancelar despesa. Os quatro CSVs usam `export=participants`, `orders`, `financial` ou `summary`, preservando `date_start` e `date_end`.

Na revisão do próprio Trilhas, foram corrigidos os acessos diretos a inscrições e credenciais sem vínculo, a consulta de área pessoal baseada em e-mail e a alteração de estado em GET. O comprador pode consultar a compra, mas não assinar pelo participante. O link de titularidade oferece um caminho explícito para o participante entrar ou criar conta, confirmar a própria titularidade e então aceitar os documentos; gerar um novo link invalida o anterior. Nenhuma mensagem é enviada automaticamente.

O aceite exige o conjunto completo das versões publicadas e vigentes selecionadas pela política central de pagamentos. Aceites antigos de usuários sem vínculo não liberam a credencial. A evidência guarda usuário, documento, data, hash e IP validado; não confia em uma cadeia arbitrária de `X-Forwarded-For`. A página de detalhe apenas consulta. O helper de ativação exige também inscrição comercialmente ativa e pedido pago; reaceitar termos não restaura credencial revogada ou expirada. As páginas pessoais, QR, confirmação e apresentação de links utilizam `Cache-Control: private, no-store` e `Referrer-Policy: no-referrer`.

A revisão coordenada com a integração de pagamentos também resultou em autorização de pedido por comprador ou sessão de compra, reserva de capacidade por lote antes da cobrança, serialização da criação por evento e expiração de pedidos abandonados. Esses comportamentos estão em `apps/checkout/views.py`, `apps/payments/services.py` e no comando `expire_checkout_orders`. Não foi feita cobrança real nesta auditoria; a validação do provedor e da configuração externa permanece separada da validação local.

A suíte integrada de publicação passou em 22/10/2026: **141 testes Django** em banco de teste isolado, além de **7 testes JavaScript da interface do scanner**. O conjunto inclui autenticação, permissões, operação, financeiro/relatórios, pagamentos com HTTP simulado, área pessoal e páginas públicas. `manage.py check` não encontrou problemas e `manage.py makemigrations --check --dry-run` não encontrou diferenças. O login, painel, financeiro e checkout indisponível foram conferidos no navegador em tamanhos desktop e móvel. A validação de locks sob concorrência PostgreSQL e a homologação real do Mercado Pago permanecem pendentes. As contagens e os estados do inventário de produção serão comparados após a implantação, preservando os registros existentes.

## O que deve permanecer específico ou depender de outra decisão

- Entidades e regras de hotelaria, quartos, diárias, housekeeping, restaurantes, cinema, floricultura e agenda de serviços não são inscrições ou benefícios de um evento.
- Emissão fiscal, certificados, séries e numeração têm contexto próprio; copiar modelos fiscais não torna o Trilhas apto a emitir documentos. O padrão de lock/idempotência é reaproveitável; o domínio fiscal não foi importado.
- Preferência de tamanho de camisa não é prova de benefício contratado ou entregue. O relatório distingue “tamanhos informados”; a entrega continua vinculada a direitos/consumos.
- Comprovantes privados, central de notificações e fila de admissão possuem referências úteis, mas não foram declarados implementados por este relatório.
- Não foram transportados scripts de deploy, usuários demonstrativos, credenciais, configurações reais de provedor, dados de terceiros, bancos ou arquivos de mídia do Naviê.
- As licenças dos fornecedores de bibliotecas e ícones continuam aplicáveis aos respectivos componentes. A autorização do proprietário foi tratada como autorização para examinar e adaptar o código do projeto, sem remover avisos de dependências.

Antes de publicar esta parte do Trilhas, aplicar a migração financeira, executar a suíte integrada com as mudanças de permissões/pagamentos e conferir o painel e as exportações em uma edição de teste. Conciliações históricas devem ser conferidas com o razão existente; o sistema sinaliza diferenças em vez de criar receitas ou despesas artificiais para fechá-las.
