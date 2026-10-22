# Padrões Naviê aplicados ao Sistema Trilhas

## Referência e critério de escolha

O estudo utilizou o código do repositório `ramilos-comunicacoes/navie-vibe`, branch `main`, commit `3a1125960a3ac259ddf111718b70886f251aea4b`. O clone de consulta fica em `references/navie-vibe` e não integra o produto distribuído. Os caminhos abaixo são relativos à raiz desse clone.

O padrão canônico adotado é o painel atual de Hotelaria, em `hoteis/templates/hoteis/base_partner.html`, combinado ao contrato de temas de `static/css/eventos-themes.css`. A referência foi aplicada aos componentes e à hierarquia do Trilhas, preservando os formulários, as URLs e as regras do domínio de eventos.

## Princípios encontrados no código

- **Esforço cognitivo mínimo:** `docs/design_philosophy.md` orienta interface útil, texto conciso, hierarquia clara e separação entre controles de gestão e experiência do participante.
- **Marca como token:** o painel de Hotelaria calcula cor e contraste por organização com `--brand-color`, `--brand-text` e `--action-contrast`. A identidade de outro cliente não deve ficar gravada em componentes reutilizáveis.
- **Tipografia atual:** `static/css/input.css` e o painel de Hotelaria usam Inter. A documentação mais antiga cita Poppins e Quicksand; ela não descreve a tipografia efetiva do painel atual. O arquivo `static/css/inter.css` da referência contém fallbacks locais, e não uma fonte Inter binária distribuída.
- **Escala de espaçamento B2B:** `static/css/input.css` e `base_partner.html` definem 36 px entre seções, 24 px entre elementos, 10 px entre rótulo e campo, 14 px entre texto e mídia e 10 px entre pills. Esses intervalos organizam telas densas sem empilhar bordas e ornamentos.
- **Superfícies e campos:** cards de cantos suaves, bordas discretas, contraste legível e foco visível. O `.input-glass` de Hotelaria combina superfície translúcida, borda e anel de foco de 4 px. A área interativa deve continuar clara nos dois temas.
- **Navegação compacta:** o painel de Hotelaria mantém uma faixa lateral de 70 px no desktop e revela a largura de 260 px por interação; cabeçalho fixo e conteúdo preservam contexto. A navegação móvel recebe adaptação própria.
- **Ícones de biblioteca:** o painel atual carrega Lucide localmente em `static/js/lucide.min.js`. Ícones de ação acompanham texto ou nome acessível; símbolos decorativos não substituem o rótulo.
- **Movimento moderado:** o contrato de temas usa transições curtas e respeita `prefers-reduced-motion`. Animação deve comunicar estado, sem disputar atenção com o conteúdo.

## Variações entre módulos Naviê

| Área de referência | Diferença encontrada | Decisão no Trilhas |
| --- | --- | --- |
| Hotelaria, `hoteis/templates/hoteis/base_partner.html` | Shell atual mais completo, tokens de marca, navegação compacta, cabeçalho translúcido e Lucide local | Referência principal para o painel interno |
| Administração | Base de gestão anterior com navegação móvel por gaveta e estrutura menos uniforme | Reutilizar hierarquia de controles e legibilidade, sem acumular outro shell |
| Eventos, `static/css/eventos-themes.css` | Contrato explícito de temas claro e glass, estados e transições | Manter consistência entre temas usando a paleta própria da Equipe Morcego |
| Login de Eventos, `eventos/templates/eventos/login.html` | Composição em duas colunas, formulário com largura máxima de 430 px, campos de 54 px e adaptação abaixo de 900 px | Referência para proporções e leitura da autenticação |
| Login de Hotelaria, `hoteis/templates/hoteis/auth/partner_login.html` | Formulário de até 420 px, rótulos compactos, ícones de 20 px; ainda contém dependências externas em parte do fluxo | Incorporar proporções e rótulos; servir os assets compartilhados localmente |
| `docs/design_philosophy.md` | Algumas escolhas tipográficas são anteriores à implementação atual | Usar seus princípios e confirmar detalhes no código vigente |

O arquivo-base de Hotelaria também contém comportamento próprio de reservas, hotéis, notificações e Alpine. Copiar esse conjunto integralmente acrescentaria acoplamento ao Trilhas. As regras visuais e os componentes comuns foram extraídos sem transplantar contratos de outro domínio.

## Contrato visual do Trilhas

- A paleta é a da logo Morcego: carvão, preto, branco, prata e laranja `#f97316`. O site público usa superfícies claras e trechos de destaque em carvão; o painel oferece temas claro e escuro. Verde, vermelho e âmbar ficam reservados a estados semânticos.
- O emblema é o PNG original fornecido pelo proprietário, com transparência. A imagem compacta usa `object-fit: contain`, sem recorte, fundo artificial, borda, sombra ou arredondamento. O favicon deriva dessa mesma imagem e recebe versão na URL para renovar o cache.
- `frontend/static/css/management.css` concentra tokens, shell e componentes comuns. `management-children.css` contém apenas a composição dos módulos, responsividade e estados específicos.
- Classes comuns: `page-heading`, `page-actions`, `card`, `section-title`, `stat-grid`, `stat-card`, `table`, `table-responsive`, `form-grid`, `form-group`, `form-label`, `form-control`, `btn`, `badge` e `empty-state`.
- Os templates filhos da gestão não carregam estilos inline. Os campos mantêm seus nomes e valores; rótulos são associados por `for`/`id`, tabelas identificam cabeçalhos e diálogos nativos oferecem foco e fechamento pelo teclado.
- Ícones usam `data-lucide` com biblioteca local e `renderTrilhasIcons` após conteúdo dinâmico. Mensagens, compartilhamento e nomes de pessoas permanecem texto simples. Não são usados emojis nativos na interface.
- Listagens preservam filtros e paginação. Valores financeiros exibidos usam formatação brasileira de centavos, sem dividir por 100 como filtro de texto nem truncar valores.
- Dados pessoais no scanner entram por `textContent`; ações usam listeners, sem compor HTML ou JavaScript com campos do participante. A interface reflete elegibilidade comercial e operacional, saldo de benefícios e revogação, mantendo a validação definitiva no servidor.

## Cuidados ao estender a interface

Novas telas devem começar pelos componentes compartilhados e pelo contexto real do módulo. Use uma ação principal por região, linguagem direta, estados vazios úteis e feedback próximo à operação. Teste conteúdo longo, telas estreitas, teclado, temas e respostas de erro. Evite cores de marca gravadas no HTML e versões paralelas de componentes já existentes.

## Assets locais e atribuição

O arquivo `frontend/static/vendor/lucide/lucide.min.js` preserva o cabeçalho original `lucide v1.8.0 - ISC`. O texto completo da [licença oficial da versão 1.8.0](https://github.com/lucide-icons/lucide/blob/1.8.0/LICENSE) está em `frontend/static/vendor/lucide/LICENSE`. Ele inclui a licença ISC de Lucide Icons and Contributors e o aviso MIT de Cole Bemis para os ícones derivados de Feather. Ambos os avisos acompanham a distribuição local.

O arquivo `frontend/static/css/fonts.css` agora fornece arquivos WOFF2 reais de Inter, Poppins e Quicksand, obtidos do Google Fonts e servidos localmente em `frontend/static/fonts/`. As licenças SIL Open Font License acompanham cada família. A área pública usa Poppins no corpo e Quicksand nos títulos, conforme a filosofia pública Navie; o painel e a autenticação usam Inter, conforme o painel atual. Nenhuma requisição ao Google Fonts é necessária no navegador.

## Revisão visual e patrocinadores — 22/10/2026

A composição pública foi refeita com referência nos blocos de `hoteis/partials/public_sections_styles.html` e `home_eventos_showcase.html`: seções de 44–80 px, mídia grande, raio de 24 px, texto curto, navegação simples e títulos sem caixa alta extensa. A escala interna foi corrigida para menus/tabelas de 14 px, campos de 48–54 px, espaçamentos de 36/24/10 px e sidebar de 70 px expandida para 264 px.

Patrocinadores são geridos por evento em **Patrocinadores → Adicionar patrocinador**. A foto/logo é opcional (PNG/JPG/WebP, até 5 MB); sua ausência produz um espaço reservado, como solicitado pelo proprietário. O campo Instagram aceita endereço HTTPS de perfil instagram.com. Quando preenchido, o cartão público mostra um botão abaixo da imagem. Nome, cota, site e ordem permanecem editáveis. Não foram fabricadas logos ou associações comerciais.
