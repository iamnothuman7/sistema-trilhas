# Padrões Naviê aplicados ao Sistema Trilhas

## Referência e critério de escolha

O estudo utilizou o código do repositório `ramilos-comunicacoes/navie-vibe`, branch `main`, commit `3a1125960a3ac259ddf111718b70886f251aea4b`. O clone de consulta fica em `references/navie-vibe` e não integra o produto distribuído. Os caminhos abaixo são relativos à raiz desse clone.

O padrão canônico adotado é o painel atual de Hotelaria, em `hoteis/templates/hoteis/base_partner.html`, combinado ao contrato de temas de `static/css/eventos-themes.css`. A referência foi aplicada aos componentes e à hierarquia do Trilhas, preservando os formulários, as URLs e as regras do domínio de eventos.

## Princípios encontrados no código

- **Esforço cognitivo mínimo:** `docs/design_philosophy.md` orienta interface útil, texto conciso, hierarquia clara e separação entre controles de gestão e experiência do participante.
- **Marca como token:** o painel de Hotelaria calcula cor e contraste por organização com `--brand-color`, `--brand-text` e `--action-contrast`. A identidade de outro cliente não deve ficar gravada em componentes reutilizáveis.
- **Tipografia atual:** `static/css/input.css` e o painel de Hotelaria usam Inter. A documentação mais antiga cita Poppins e Quicksand; ela não descreve a tipografia efetiva do painel atual. O arquivo `static/css/inter.css` da referência contém fallbacks locais, e não uma fonte Inter binária distribuída.
- **Escala de espaçamento B2B:** `static/css/input.css` e `base_partner.html` definem 36 px entre seções, 24 px entre elementos, 10 px entre rótulo e campo, 14 px entre texto e mídia e 10 px entre pills. Esses intervalos organizam telas densas sem empilhar bordas e ornamentos.
- **Superfícies e campos:** cards de cantos suaves, bordas discretas, contraste legível e foco visível. O `.input-glass` de Hotelaria combina superfície translúcida, borda e anel de foco. No Trilhas, campos recebem fundo mais opaco para preservar a leitura no tema escuro.
- **Navegação compacta:** o painel de Hotelaria mantém uma faixa lateral de 70 px no desktop e revela a largura de 260 px por interação; cabeçalho fixo e conteúdo preservam contexto. A navegação móvel recebe adaptação própria.
- **Ícones de biblioteca:** o painel atual carrega Lucide localmente em `static/js/lucide.min.js`. Ícones de ação acompanham texto ou nome acessível; símbolos decorativos não substituem o rótulo.
- **Movimento moderado:** o contrato de temas usa transições curtas e respeita `prefers-reduced-motion`. Animação deve comunicar estado, sem disputar atenção com o conteúdo.

## Variações entre módulos Naviê

| Área de referência | Diferença encontrada | Decisão no Trilhas |
| --- | --- | --- |
| Hotelaria, `hoteis/templates/hoteis/base_partner.html` | Shell atual mais completo, tokens de marca, navegação compacta, cabeçalho translúcido e Lucide local | Referência principal para o painel interno |
| Administração | Base de gestão anterior com navegação móvel por gaveta e estrutura menos uniforme | Reutilizar hierarquia de controles e legibilidade, sem acumular outro shell |
| Eventos, `static/css/eventos-themes.css` | Contrato explícito de temas claro e glass, estados e transições | Reaproveitar o vidro e a hierarquia, com tema exclusivamente escuro e paleta Morcego |
| Login de Eventos, `eventos/templates/eventos/login.html` | Composição em duas colunas, formulário com largura máxima de 430 px, campos de 54 px e adaptação abaixo de 900 px | Referência para proporções e leitura da autenticação |
| Login de Hotelaria, `hoteis/templates/hoteis/auth/partner_login.html` | Formulário de até 420 px, rótulos compactos, ícones de 20 px; ainda contém dependências externas em parte do fluxo | Incorporar proporções e rótulos; servir os assets compartilhados localmente |
| `docs/design_philosophy.md` | Algumas escolhas tipográficas são anteriores à implementação atual | Usar seus princípios e confirmar detalhes no código vigente |

O arquivo-base de Hotelaria também contém comportamento próprio de reservas, hotéis, notificações e Alpine. Copiar esse conjunto integralmente acrescentaria acoplamento ao Trilhas. As regras visuais e os componentes comuns foram extraídos sem transplantar contratos de outro domínio.

## Contrato visual do Trilhas

- **Decisão do proprietário em 22/10/2026: somente tema escuro em todo o aplicativo.** Site, gestão e autenticação fixam `data-theme="dark"`; não há alternância claro/escuro nem leitura de preferência de tema armazenada. A identidade combina carvão quente, terra, laranja (`#f97316` na gestão e `#ff7a28` no site), vermelho e texto branco quente. Verde continua reservado a sucesso; avisos e erros mantêm texto e ícone além da cor.
- O site usa Inter nos títulos e Poppins no corpo; gestão e autenticação usam Inter. Títulos públicos podem ter peso 800 e ênfase inclinada em laranja, mantendo a leitura e a hierarquia.
- O PNG original do emblema permanece intacto. Suas versões WebP locais preservam desenho, proporção e transparência: `logo-nav.webp` (192 × 159) e `logo-display.webp` (640 × 531). A imagem usa `object-fit: contain`, sem recorte, fundo artificial, borda, sombra, filtro ou arredondamento. `favicon-64.png` deriva da mesma arte.
- O efeito de vidro combina preenchimentos translúcidos, reflexo suave, borda clara e sombra difusa. Cards, tabelas, diálogos e áreas internas não recebem desfoque de fundo em série. O site limita `backdrop-filter` à navegação (14 px no desktop, 10 px no celular); a gestão usa 24 px na navegação, e o login aplica 24 px somente à superfície do formulário. Gradientes estáticos de terra, laranja e vermelho tornam essas camadas perceptíveis.
- A textura local `img/trail-tread.svg` representa marcas de pneu: é decorativa, discreta e nunca cobre conteúdo com alto contraste. Na autenticação aparece apenas no painel de marca do desktop, estática e com baixa opacidade. Não se aplicam filtros ou texturas sobre a própria logo.
- `frontend/static/css/trail.css` é a camada visual pública vigente sobre `styles.css` e `public-pages.css`; seus tokens escuros e títulos Inter substituem os valores anteriores. Novas páginas públicas devem herdar esses tokens, evitando fundos brancos ou cores de texto gravadas no template.
- `frontend/static/css/management.css` concentra tokens, shell e componentes comuns. `management-children.css` contém apenas a composição dos módulos, responsividade e estados específicos.
- Classes comuns: `page-heading`, `page-actions`, `card`, `section-title`, `stat-grid`, `stat-card`, `table`, `table-responsive`, `form-grid`, `form-group`, `form-label`, `form-control`, `btn`, `badge` e `empty-state`.
- Os templates filhos da gestão não carregam estilos inline. Os campos mantêm seus nomes e valores; rótulos são associados por `for`/`id`, tabelas identificam cabeçalhos e diálogos nativos oferecem foco e fechamento pelo teclado.
- Ícones usam `data-lucide` com biblioteca local e `renderTrilhasIcons` após conteúdo dinâmico. Mensagens, compartilhamento e nomes de pessoas permanecem texto simples. Não são usados emojis nativos na interface.
- Listagens preservam filtros e paginação. Valores financeiros exibidos usam formatação brasileira de centavos, sem dividir por 100 como filtro de texto nem truncar valores.
- Dados pessoais no scanner entram por `textContent`; ações usam listeners, sem compor HTML ou JavaScript com campos do participante. A interface reflete elegibilidade comercial e operacional, saldo de benefícios e revogação, mantendo a validação definitiva no servidor.

## Cuidados ao estender a interface

Na gestão, **Eventos → Gerenciar → Dados e publicação** reúne identificação, local, datas, fuso, capacidade e atendimento. A criação exige administração geral e sempre começa em rascunho; gestores de uma edição podem editar somente seus eventos. O endereço existente é preservado. A capacidade deve comportar reservas e vagas confirmadas e é atualizada junto ao controle usado pelo checkout, sem alterar contadores ou histórico.

**Aparência da edição** permite enviar ou retirar banner e logo, com permissão de conteúdo da mesma edição. Uploads PNG, JPG e WebP são validados antes da gravação, com limites de 15 MB e 24 megapixels; os arquivos anteriores não são apagados ao trocar a referência. Não existe editor de CSS arbitrário. **Relatórios** oferece resumo, pedidos, extrato e participantes em CSV, com filtros interpretados no fuso do evento; a exportação de participantes também exige permissão de inscrições. O papel financeiro isolado não concede acesso a esse CSV.

O relatório administrativo também agrupa inscrições ativas por equipe e por cidade/UF, sempre dentro da edição e do período de criação selecionado. As tabelas exibem apenas nomes dos grupos e quantidades, sem dados individuais; espaços nas extremidades são removidos, e valores vazios ou marcados como “Não informado” são omitidos. Não há publicação de ranking no site público.

Novas telas devem começar pelos componentes compartilhados e pelo contexto real do módulo. Use uma ação principal por região, linguagem direta, estados vazios úteis e feedback próximo à operação. Teste conteúdo longo, telas estreitas, teclado e respostas de erro. Evite cores de marca gravadas no HTML e versões paralelas de componentes já existentes.

O tema escuro não depende da preferência de cor do sistema. Preserve foco visível, rótulos, mensagens acessíveis, áreas de toque adequadas e contraste de campos e texto secundário. `prefers-reduced-transparency` troca o vidro por superfícies sólidas; a ausência de suporte a `backdrop-filter` também possui fallback opaco. `prefers-reduced-motion` desativa animações e transições. Painel e autenticação não usam animações contínuas. Efeitos decorativos existentes no site devem parar automaticamente fora da área visível, com a página oculta ou sob `prefers-reduced-motion`, sem controles de pausa ou movimento na interface.

Os campos de cartão hospedados pelo SDK Mercado Pago mantêm um fundo neutro claro para o texto interno do provedor. O `color-scheme: light` desse componente isolado não oferece tema claro ao site nem altera o restante do checkout.

## Reauditoria visual — 22/10/2026

A nova comparação percorreu `docs/design_philosophy.md`, `static/css/input.css`, `hoteis/templates/hoteis/base_partner.html`, `hoteis/templates/hoteis/auth/partner_login.html` e `eventos/templates/eventos/login.html`. Foram confrontados com o shell, os componentes CSS e a autenticação atuais do Trilhas. A referência de Hotelaria usa Inter, faixas de 70/260 px, cabeçalho de 64 px, ritmo B2B de 36/24/10 px, cards de 16–24 px e campos glass com foco de marca. O login de Eventos limita o formulário a 430 px e usa campos de 54 px; Hotelaria utiliza 50 px e texto de campo a 16 px. O Trilhas mantém proporções equivalentes, adaptadas à marca Morcego.

| Ponto verificado | Resultado e ajuste pontual |
| --- | --- |
| Hierarquia e componentes | Tokens de marca, Inter local, cards, tabelas, estados vazios e espaçamento já estavam centralizados. Nenhum segundo sistema de componentes foi acrescentado. |
| Página atual na navegação | O estado ativo aparecia só como classe de cor. O shell agora também fornece `aria-current`; a barra móvel mantém Inscrições selecionada ao consultar uma inscrição. |
| Fechamento do menu móvel | A gaveta dependia do fundo externo ou Escape. Um botão textual **Fechar menu**, no rodapé da própria gaveta, reutiliza o controlador existente: mantém `aria-expanded`, restauração do foco e fechamento por Escape. O botão fica oculto no desktop. |
| Foco e erros de campo | `.form-control:focus` apagava o contorno geral de teclado e deixava apenas a borda e uma sombra translúcida. `:focus-visible` agora conserva um contorno opaco. Campos que o formulário marca com `aria-invalid="true"` recebem borda e foco de erro, além da mensagem já existente. |
| Cabeçalho e títulos longos | A área de ações fica protegida da compressão; títulos e introduções podem quebrar palavras longas sem alargar a página. A rolagem reserva espaço para o cabeçalho fixo e para a barra móvel. |
| Controles móveis | Botão de abrir menu e menu da conta têm área de 44 px. O rodapé da gaveta respeita a área segura do aparelho e a rolagem da navegação fica contida. |
| Autenticação | Rótulos, autocomplete, mostrar/ocultar senha com nome e estado acessíveis, erros anunciados e campos de 16 px já estavam presentes. A composição em duas colunas e a adaptação móvel seguem as proporções da referência; não foi alterada nesta rodada. |
| Vidro, tema e dependências | Mantidos tema escuro fixo, fontes e Lucide locais, logo transparente sem moldura e fallback sólido do vidro. Não foram importados alternador de tema, chat, notificações, Alpine, HTMX ou animações contínuas do painel hoteleiro. |

As diferenças remanescentes são deliberadas: paleta Morcego em lugar do azul/roxo dos logins Naviê; sidebar expandida de 264 px; nomes e fluxos do domínio de trilhas; campos mais opacos para leitura. A referência também contém padrões que não devem ser copiados automaticamente, como foco removido em alguns botões de ícone, dependências externas e a alternância de tema. Esta auditoria de código não equivale a uma certificação completa de acessibilidade. A revisão no navegador confirmou ausência de transbordamento nas larguras de 360, 768 e 1280 px; no celular, abrir/fechar menu, Tab/Shift+Tab, Escape e restauração do foco funcionaram. O formulário inválido manteve os dados e exibiu mensagem associada e borda de erro.

## Assets locais e atribuição

O arquivo `frontend/static/vendor/lucide/lucide.min.js` preserva o cabeçalho original `lucide v1.8.0 - ISC`. O texto completo da [licença oficial da versão 1.8.0](https://github.com/lucide-icons/lucide/blob/1.8.0/LICENSE) está em `frontend/static/vendor/lucide/LICENSE`. Ele inclui a licença ISC de Lucide Icons and Contributors e o aviso MIT de Cole Bemis para os ícones derivados de Feather. Ambos os avisos acompanham a distribuição local.

O arquivo `frontend/static/css/fonts.css` fornece arquivos WOFF2 locais. A composição vigente usa Inter nos títulos públicos, no painel e na autenticação, e Poppins no corpo do site. Quicksand permanece entre os assets anteriores, mas não define os títulos do tema público atual. As licenças SIL Open Font License acompanham as famílias em `frontend/static/fonts/`. Nenhuma requisição ao Google Fonts é necessária no navegador.

## Revisão visual e patrocinadores — 22/10/2026

A composição pública conserva a geometria dos blocos de `hoteis/partials/public_sections_styles.html` e `home_eventos_showcase.html`, adaptada à identidade de trilhão: hero com mídia ampla, marcas de pneu discretas, seções espaçosas, cantos de 22–28 px e texto curto. A escala interna mantém menus/tabelas de 14 px, campos de 48–54 px, espaçamentos de 36/24/10 px e sidebar de 70 px expandida para 264 px.

Patrocinadores são geridos por evento em **Patrocinadores → Adicionar patrocinador**. A foto/logo é opcional (PNG/JPG/WebP, até 15 MB); sua ausência produz um espaço reservado, como solicitado pelo proprietário. O campo Instagram aceita endereço HTTPS de perfil instagram.com. Quando preenchido, o cartão público mostra um botão abaixo da imagem. Nome, cota, site e ordem permanecem editáveis. Não foram fabricadas logos ou associações comerciais.

Novos uploads válidos, pelo formulário compartilhado entre gestão e Admin, geram uma prévia WebP de até 800 × 800 px (`quality=85`, sem ampliar imagens pequenas). A orientação EXIF é aplicada; proporção e transparência são preservadas. A migração `events.0005_sponsor_logo_preview` acrescenta o arquivo opcional e suas dimensões. `display_logo_url` prioriza a prévia e usa a imagem original quando ela não existe. O grid público usa carregamento adiado, decodificação assíncrona e dimensões persistidas. Não há conversão em GET nem reprocessamento automático de imagens existentes; editar um patrocinador sem novo upload preserva a mídia. A remoção da imagem também limpa a referência de prévia.

## Reauditoria funcional — 22/10/2026

A rodada releu permissões, gestão de equipe, gravação de eventos e relatórios do clone Navie identificado no início do documento. A comparação cobre os trechos examinados, sem afirmar cobertura integral dos sistemas. Não foram importados usuários, credenciais ou dados de clientes.

| Fonte Navie consultada | Regra útil | Aplicação e limite no Trilhas |
| --- | --- | --- |
| `eventos/access.py` e `hoteis/hotel_access.py:24` | Resolver a organização selecionada dentro das organizações autorizadas; consultar as capacidades nesse contexto | `apps/core/permissions.py` mantém papéis por edição e recusa ações sem política cadastrada. Uma função em uma edição não concede o mesmo papel em outra |
| `hoteis/permissions.py:16` e `hoteis/team_views.py:47` | Política central para módulos e ações; revalidação sob bloqueio da organização | Reutilizado como princípio, sem copiar o retorno permissivo para capacidades não configuradas presente na Hotelaria. O Trilhas mantém negação por padrão |
| `hoteis/team_views.py:32` | Respostas administrativas privadas e sem cache | Aplicado também à visão geral nesta rodada; relatórios, edição e aparência já bloqueavam cache |
| `eventos/views.py:435` | Validar campos e mídia antes da gravação; alteração e auditoria na mesma transação | Já presente na edição/criação e aparência. Datas usam o fuso da edição; capacidade preserva reservas e ativos legados; referências anteriores de mídia não são apagadas |
| `hoteis/report_filters.py:6` e `eventos/views.py:646` | Filtros seguem organização e período; exportação exige capacidade financeira | Relatório e CSV compartilham filtros por edição e período. Exportar participantes exige também a capacidade de inscrições. Financeiro usa centavos inteiros e considera estornos |

**Correção entregue:** a visão geral consultava e mostrava nomes de compradores e participantes para qualquer papel do painel, embora os módulos correspondentes fossem restritos. Agora a consulta e o cartão de últimos pedidos exigem financeiro ou inscrições; últimas presenças exigem credenciamento. Conteúdo e logística mantêm indicadores agregados e ações autorizadas. A checagem usa a edição selecionada, inclusive quando a pessoa tem funções diferentes entre edições. O cabeçalho da tabela identifica corretamente o comprador, que pode ser diferente do participante.

Os indicadores operacionais contam como prontos/credenciados somente inscrições comercialmente ativas. O valor pendente a receber desconsidera reservas vencidas. Esse cálculo não apaga presenças, altera pedidos ou reconcilia dados legados.

### Fonte Nabiobjj efetivamente consultada

A cópia selecionada pelo auditor está em `references/nabiobjj-audit/source`. Trata-se de fonte Django encontrada no servidor; a inspeção operacional informou que o domínio atual usa um aplicativo Java. Portanto, esta leitura não prova que o código Django examinado seja o serviço ativo.

- `apps/tenancy/views.py:34–75` decide o papel antes de montar consultas financeiras e separa o painel pessoal do administrativo. `apps/tenancy/middleware.py:116` entrega capacidades distintas aos templates. Isso reforça a correção do dashboard: restringir os dados no servidor, além de esconder controles.
- `apps/financial/ledger_views.py:17–27` limita o período de consulta e trata entradas inválidas. O `DateRangeForm` do Trilhas adotou os anos **1900 a 2199**, com erro de campo e limites nos inputs, para evitar estouro na conversão de datas extremas para UTC. O limite de dez anos do produto de academia não foi importado. Relatório, financeiro e CSV usam o mesmo formulário.
- `apps/financial/ledger_views.py:57–62` verifica a versão da edição sob bloqueio antes de salvar. É uma referência técnica para prevenir sobrescrita entre duas abas; sua adaptação ao editor de eventos é tratada separadamente. Graduações, categorias de luta e mensalidades de academia não integram o domínio do Trilhas.

Validação desta rodada: `tests/test_management_access.py` testa seis papéis, listas fornecidas pelo servidor, dados renderizados, cache e troca de edição com capacidades diferentes. `tests/test_reporting_finance.py` testa indicadores sem mutar histórico e rejeição de datas extremas em relatório, financeiro e exportações, além de aceitar os limites documentados.

**Paginação entregue:** os cortes de 150 inscrições e 100 atividades foram substituídos por páginas de 40 registros, seguindo o uso de `Paginator` em `references/nabiobjj-audit/source/apps/financial/ledger_views.py:31–42`. A inscrição preserva busca, situação comercial e operacional ao navegar; a auditoria oferece busca por ação, responsável, entidade ou motivo, sempre dentro das edições autorizadas. O partial `gestao/partials/pagination.html` compartilha total, primeira/anterior/próxima/última página e estado vazio. A ordenação inclui a chave primária para desempatar registros; entradas de página inválidas usam o tratamento padrão do Django. Os seis testes de `tests/test_management_pagination.py` passaram: percorrem 167 inscrições e 125 atividades sem perda/duplicação e verificam filtros, escopo, permissões, páginas inválidas e resultados vazios. As duas listagens bloqueiam cache.

Lacunas úteis identificadas, sem promessa de implementação nesta rodada:

- A administração de vínculos da equipe existe no Django Admin (`apps/identity/admin.py`), sem tela própria por edição. O fluxo de equipe do Navie é referência, mas precisa de política explícita de delegação e testes contra aumento de privilégio antes de ser adaptado.
- Navie Eventos oferece PDF e XLSX; o Trilhas oferece CSV. Outros formatos devem manter os mesmos filtros, valores exatos e proteção do conteúdo, sem importar categorias de outra operação.
