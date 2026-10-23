# Auditoria do NaBio BJJ e aproveitamento no Sistema Trilhas

Data: 22/10/2026. Consulta autorizada, somente leitura, ao servidor que também hospeda o Trilhas. O resultado principal é distinguir duas referências: o domínio público aponta para uma aplicação Java ativa; o projeto Django disponível no mesmo servidor está inativo e serve como referência de código anterior. Os padrões adaptados ao Trilhas abaixo vieram dessa fonte Django, não de uma suposição sobre a implementação Java.

## Origem e limites da análise

| Referência | Evidência coletada | Limite |
| --- | --- | --- |
| `/var/www/apps/nabiobjj` | Projeto Django com `apps/`, `templates/`, `static/`, documentação e testes; `nabiobjj.service` inativo | Código anterior, não comprova o comportamento do domínio atual |
| `/var/www/apps/nabiobjj-java/current` | Aponta para `releases/java-migration-20261020-r3`; contém `app.jar`; `nabiobjj-java.service` ativo | O artefato disponível não contém fontes `.java` ou `.kt` |
| Nginx de `bjj.nabio.pro` | HTTPS encaminhado a `127.0.0.1:8061`; limite multipart de `6m` | Foi lido somente o roteamento relevante; não houve alteração de configuração |
| Artefato Java | SHA-256 `5bde2423eab10f09805007d4ecd0c2bf53f76359c5f70ee765d142c9e921cfbd`, 109.474.951 bytes, 91 classes da aplicação | Hash do arquivo apontado pela release; não é auditoria de memória do processo nem certificação de segurança |

A cópia selecionada está em `references/nabiobjj-audit/source/`: 292 arquivos, sendo 109 Python, 104 HTML, 34 JavaScript, 28 CSS, 16 documentos Markdown e um arquivo de dependências. O arquivo de settings foi sanitizado, incluindo declarações condicionais de banco, para omitir valores de configuração sensíveis. Não foi executado como configuração da aplicação.

Foram extraídos do JAR somente 165 templates/CSS/JavaScript em `references/nabiobjj-audit/java-resources/`. O JAR completo não foi copiado. Dos 164 caminhos também presentes na amostra Django, 67 recursos são idênticos após normalização de quebras de linha; 62 deles são CSS/JavaScript. Isso demonstra reaproveitamento de interface, não equivalência dos serviços de negócio.

Inventários e hashes estão em `source-inventory.json`, `manifest.json`, `operational-source.json` e `java-artifact-inventory.json`, dentro da referência ignorada pelo Git. Não foram consultados bancos, registros pessoais, arquivos de ambiente, chaves, uploads, mídia de alunos, logs ou cópias de dados. Não foram executados seed, importação, manutenção, scripts do projeto ou testes contra o servidor. Os helpers de publicação congelados do Trilhas permaneceram intactos.

## Arquitetura encontrada

A fonte Django declara Django 5.2.15, Django OAuth Toolkit 3.4.1, Gunicorn, WhiteNoise e Pillow em `requirements.txt`. O monólito separa contas e vínculos, academias, alunos, turmas, presenças, progressão esportiva, financeiro, portal pessoal e vitrine pública. `nabiobjj/urls.py` reúne essas áreas, uma API de identidade e endpoints públicos de instalação e analytics.

O artefato Java inclui Spring Boot 4.0.8, Spring Security, OAuth2 Authorization Server, Thymeleaf, Hibernate/JPA, Flyway e drivers PostgreSQL/H2. São dependências observadas no pacote, não uma afirmação de que todos os módulos ou bancos estão habilitados em produção. As classes se organizam em `pro.nabio.bjj.web`, `service`, `security`, `data`, `model` e `view`; há controladores de academias, contas, atletas, turmas, financeiro, identidade e plataforma, além de serviços correspondentes. `Access`, `SessionGuard` e `PkceGuard` indicam componentes de controle de acesso, mas nomes de classes não comprovam suas regras.

Os templates Java extraídos usam Thymeleaf e uma camada própria `ui.eval`, `ui.output` e `ui.url`. Eles não são templates Django intercambiáveis. Não se deve transplantar esse mecanismo de renderização ou o JAR para o Trilhas. Para auditar autorização, transações e reconciliação do Java em profundidade, será necessário o repositório-fonte da release e seus testes; essa equivalência não foi presumida.

## Padrões úteis, com evidência de código

Nesta seção, os caminhos são relativos a `references/nabiobjj-audit/source/`.

| Padrão | Evidência | Aplicação no Trilhas |
| --- | --- | --- |
| Separar contexto e autorização | `apps/tenancy/middleware.py:27–116` resolve academia, valida vínculos e impede substituição de um host específico por outra academia | Manter evento explícito na URL e autorização por edição; seleção visual ou sessão não concede acesso |
| Capacidades antes das consultas | `apps/tenancy/views.py:34–75` redireciona FINANCE e só monta consultas financeiras para OWNER/SAAS_ADMIN; `apps/tenancy/middleware.py:106–122` expõe capacidades à interface | Montar listas de compradores e presenças somente para os papéis correspondentes; exibir indicadores financeiros apenas com a capacidade financeira |
| Formulário com versão | `apps/financial/ledger_views.py:54–62` trava academia/lançamento e compara `updated_at` recebido no campo `version` | Recusar edição de evento feita em aba antiga antes de sobrescrever dados e capacidade |
| Datas validadas antes da consulta | `apps/financial/ledger_views.py:17–27` recusa ordem invertida, anos fora de 1900–2199 e período excessivo | Validar limites seguros no formulário compartilhado de relatórios/financeiro; evitar overflow de calendário/fuso horário |
| Caixa separado de previsão | `apps/financial/overview.py:17–25,43–50` usa data do pagamento no caixa e vencimento na previsão; não duplica mensalidades no razão | Preservar receita efetiva, despesas pendentes e compromissos como indicadores distintos, com a semântica de cada filtro visível |
| Preservar o preço contratado | `apps/financial/services.py:13–24` retorna cobrança existente; `apps/financial/views.py:133–173` altera contrato para novas cobranças | Alterar lotes futuros sem reprecificar `Order` ou `OrderLine` já emitidos; coerente com a oferta autorizada de R$ 120,00 |
| Ajustes financeiros com histórico | `apps/financial/models.py:194–218` registra movimentos de diferença em `LedgerPayment` | Manter lançamentos financeiros imutáveis e correções explícitas; não substituir o razão do Trilhas pelo modelo de mensalidades |
| Convite de uso único | `apps/accounts/services.py:137–175` guarda hash do token, expiração e consumo; revalida o emissor e o vínculo sob transação | Referência para convites de equipe e links de titularidade; manter o fluxo pessoal e o aceite legal do Trilhas separados da compra |
| Delegação restrita | `apps/athletes/services.py:125–150` concede somente chamada, impede autoalteração, exige conta/vínculo ativo e não duplica histórico em um envio sem mudança | Operador de credenciamento não deve receber edição geral ou financeiro; revogação precisa valer na próxima operação do backend |
| Consulta incremental autorizada | `apps/attendance/live.py:13–39` projeta a lista por academia, usa ETag e `private, no-store`; `static/js/attendance-live.js:45–77` cancela consultas antigas e serializa alterações | Referência futura para atualização de check-in, com evento/credencial validados no servidor e UUID de intenção já existente no Trilhas |
| Filtros sem perder contexto | `static/js/live-filters.js:15–54` cancela respostas antigas, mantém os filtros, anuncia falha e reinicia paginação | Melhoria de interação possível mantendo GETs legíveis e submetendo alterações apenas por ação explícita |
| Paginação real do conjunto filtrado | `apps/financial/ledger_views.py:31–42` usa `Paginator` de 40 itens depois dos filtros; `apps/financial/views.py:20–46` aplica o mesmo princípio às faturas | Permitir percorrer todas as inscrições e atividades autorizadas, preservando busca e filtros, em vez de cortar os primeiros registros |
| Identidade com escopos | `apps/accounts/identity.py:19–45,54–90` exige fluxo de código, PKCE S256, redirect registrado, usuário ativo e escopos; e-mail é explicitamente não verificado | Útil apenas se houver futura integração de identidade; não vincular pessoas automaticamente por e-mail nem copiar clientes/chaves OAuth |
| Offline sem identidade | `apps/core/pwa.py:42–67` renderiza a tela offline sem request e monta uma lista de precache restrita | Se houver PWA para operação de campo, jamais armazenar indiscriminadamente páginas autenticadas, QR ou financeiro |

O financeiro BJJ usa `Decimal` em reais e contratos recorrentes; o Trilhas usa centavos inteiros e inscrições por edição. Reutilizar regras de validação, autorização e preservação de histórico é apropriado. Importar tabelas de mensalidades, graduação, faixas, pontuação ou matrícula criaria outro domínio dentro do sistema.

## Adaptações concretas nesta rodada

As mudanças abaixo foram implementadas pelos responsáveis pelo código do Trilhas após o encaminhamento dos achados. Não são promessas futuras nem indicação de que o Nabiobjj foi alterado.

1. **Edição de evento sem sobrescrita por aba antiga.** `apps/events/forms_management.py:29,86,129–135` recebe a versão de `updated_at`; `apps/core/views_gestao.py:125–150` reconsulta o evento sob lock antes de validar. Uma versão antiga, ausente ou inválida retorna HTTP 409, conserva o conteúdo enviado para conferência e não modifica evento, capacidade ou histórico. As três regressões novas cobrem conflito entre abas, versão inválida/ausente e correção de um formulário com erro comum. A suíte de 17 testes de gestão de eventos passou, conforme execução do agente responsável.
2. **Intervalos de relatório seguros.** `apps/reporting/forms.py` passou a limitar ambas as datas a 01/01/1900–31/12/2199, no backend e nos atributos dos campos. O mesmo formulário protege financeiro, relatórios e exportações. Foram verificados extremos de calendário, caminhos de exportação e financeiro; os 28 testes de `test_reporting_finance` passaram na execução responsável. Não foi importado o limite comercial de dez anos do BJJ, que não é requisito do Trilhas.
3. **Dados do dashboard conforme a capacidade.** A revisão do Navie já havia identificado exposição excessiva de contexto; o padrão BJJ de autorizar antes de consultar reforçou a correção no Trilhas. As listas recentes de compradores/pedidos e presenças agora dependem das capacidades correspondentes da edição, incluindo as consultas dessas listas e o contexto. A função geral de KPIs ainda calcula agregados financeiros no servidor; o template não os exibe aos papéis sem capacidade financeira. Portanto, não se afirma que todas as consultas financeiras foram eliminadas desses requests. Os testes de gestão/relatórios utilizados nessa correção passaram.
4. **Inscrições e auditoria sem cortes que escondem registros antigos.** As duas listagens agora usam páginas de 40 itens, com componente reutilizável em `frontend/templates/gestao/partials/pagination.html`. Busca, situação e escopo autorizado acompanham a navegação; a auditoria também permite pesquisar ação, responsável, entidade e motivo. Os seis testes de `tests/test_management_pagination.py` passaram com conjuntos de 167 inscrições e 125 atividades, verificando navegação sem perdas/duplicação, isolamento, filtros, páginas inválidas e estados vazios.

Esses testes são locais e independentes de dados reais. A execução integrada informada pelo responsável passou 236 testes antes da última adaptação de paginação; os seis testes dessa adaptação passaram separadamente. Não se somam essas execuções como se constituíssem uma nova bateria integrada. A proteção de aba antiga foi exercitada funcionalmente; ela não é uma prova de carga concorrente em todas as combinações de banco e workers. Nesta auditoria não houve cobrança, criação de conta real ou publicação.

## Cuidados e lacunas na referência anterior

Os itens seguintes são observações sobre a fonte Django inativa. Não foram atribuídos ao Java atual sem evidência de código equivalente.

### Consulta do dashboard faz gravações

`apps/tenancy/views.py:52` chama `refresh_academy_readiness`; em `apps/martial_arts/views.py:14–17`, a função percorre progressões e executa `save()` para cada uma. Visitar um dashboard pode, portanto, disparar gravações e sinais de auditoria, com custo proporcional aos alunos. Não copiar esse padrão para GETs do Trilhas. Preferir projeções de leitura ou atualização explícita após uma mudança que realmente afete o estado.

### A auditoria genérica captura mais dados que o necessário

`apps/core/audit.py:10,30–38,48–64` ignora alguns campos e mascara a senha, mas monta snapshots dos demais campos concretos e rótulos textuais. `apps/students/models.py:33–59` inclui documentos, endereço, contatos de responsáveis e informações médicas. A combinação permite replicar esses campos para o histórico de alterações, sem uma lista positiva específica do domínio. Isso foi constatado pelo código, sem consultar qualquer registro. No Trilhas, manter eventos de auditoria explícitos com IDs, campos alterados e transição de estado; não copiar snapshots pessoais completos como padrão.

### A busca aproximada percorre todo o conjunto autorizado

`apps/core/search.py:27–37` itera IDs/colunas, calcula similaridade em Python e gera uma expressão SQL `CASE` por resultado. A autorização continua no queryset de entrada, mas custo de CPU/memória e tamanho da consulta crescem com o conjunto pesquisado. Não importar essa busca inteira para inscrições de grande volume. Usar filtros/paginação indexados primeiro e estabelecer um limite explícito se a busca aproximada for necessária.

### O conversor de imagens genérico não tem todas as proteções do Trilhas

`apps/core/forms.py:60–70` limita bytes do upload, mas `apps/core/webp_optimizer.py:32–54` converte o modo antes de reduzir a imagem, não impõe teto próprio de pixels nesse caminho e não corrige orientação EXIF. Pode consumir memória excessiva em imagens compactadas com grande resolução e exibir fotos rotacionadas. O caminho específico de fotos de presença em `apps/athletes/services.py:26–39` já tem validação de pixels e transposição; os dois caminhos não são equivalentes. Preservar no Trilhas as validações de cabeçalho, limite de pixels, preservação do original e limpeza de arquivos novos em falha de transação já implementadas.

### Deduplicação não é uniforme entre fluxos

`apps/financial/ledger_views.py:61–65` detecta a chave repetida de criação do lançamento e redireciona, sem comparar o conteúdo enviado com um hash. A criação manual de fatura em `apps/financial/views.py:98–109` apenas valida e salva; a regra de retornar a cobrança já existente de `services.generate_month` não é usada por esse caminho. Esses códigos não devem ser usados como substitutos do controle de idempotência do Trilhas, que precisa distinguir repetição da mesma intenção de reutilização indevida da chave e preservar suas constraints.

### Documentação de testes e desempenho não equivale a medição atual

A amostra selecionada contém 39 métodos `test_*` em seis arquivos de teste. Os documentos citam outras baterias e medições históricas de Lighthouse, mas seus relatórios completos não foram coletados nem reexecutados. Não atribuir ao Trilhas notas de desempenho, percentuais de melhoria ou quantidade de testes do BJJ. Avaliar as páginas e operações efetivamente adaptadas, no seu próprio ambiente.

## Nova conferência da fonte do Naviê

A pasta encontrada é `/var/www/apps/navievibe`. Não foi obtida uma revisão Git dessa instalação. Foram comparados 328 caminhos selecionados por SHA-256, normalizando somente quebras de linha:

- 323 arquivos presentes no servidor; todos iguais à amostra anterior em `references/navie-vibe-server`.
- Frente à referência GitHub `3a1125960a3ac259ddf111718b70886f251aea4b`: 218 desses arquivos idênticos, 98 presentes nas duas fontes porém diferentes e sete existentes somente na amostra do servidor.
- Continuam ausentes no servidor os cinco caminhos já apontados na auditoria anterior: `eventos/access.py`, `eventos/checkout.py`, `hoteis/hotel_access.py`, `financeiro/oauth_state.py` e `financeiro/attachments.py`.

`references/nabiobjj-audit/navie-source-comparison.json` registra cada comparação sem conteúdo de configuração. Essa conferência confirma a proveniência da amostra anterior; não transforma a instalação do servidor no checkout completo do GitHub, nem é uma revisão de todas as rotas executadas. As recomendações fundamentadas na fonte GitHub devem continuar identificadas como tal.

## Próximos aproveitamentos recomendados

Priorizar melhorias que preservem o domínio do Trilhas: consulta incremental de credenciamento com permissões revalidadas, estado vazio e filtros consistentes; redução dos ícones públicos ao subconjunto usado; versionamento de assets por conteúdo; convites de equipe com papéis explícitos e uso único. Cada uma exige sua adaptação e validação própria. Não importar automaticamente o provider de identidade, tabelas esportivas, dados de academias, fotos de atletas, política de mensalidades ou regras de graduação.

O repositório-fonte Java da release ativa é a referência necessária para uma próxima auditoria completa do backend do Nabiobjj atual. Enquanto isso, o material coletado permite reaproveitar conscientemente os padrões Django identificados e os recursos públicos inspecionados, mantendo explícita essa limitação.

## Validação integrada da entrega

Após integrar todas as adaptações, a suíte completa do Trilhas passou com **242 testes Django**. Os oito testes do runner Node também passaram, cobrindo a seleção de variações e os totais da inscrição, além das ações de credenciamento/entrega. `manage.py check`, a verificação de migrações pendentes e `git diff --check` não apontaram problemas. A revisão de navegador cobriu páginas públicas em 320/768/1280 px e painel em 360/768/1280 px; verificou imagens, ausência de rolagem lateral, navegação móvel, foco, validação de formulário e busca da auditoria. Não houve teste de cobrança real nem medição de Lighthouse nesta entrega.
