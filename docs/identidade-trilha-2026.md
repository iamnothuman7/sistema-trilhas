# Identidade de trilha — outubro de 2026

Direção aprovada pelo proprietário: somente tema escuro, terra, laranja e vermelho; logo original sem texto HTML ao lado no cabeçalho público; navegação em cápsula com vidro; referências de pneus e trilha; movimento automático sem botão de pausa.

## Referências consultadas

- [Awwwards: Lando Norris](https://www.awwwards.com/sites/lando-norris): identidade esportiva expressiva e interação ligada ao esporte. A paleta da referência não foi copiada.
- [Awwwards: Sports](https://www.awwwards.com/websites/sports/): pesquisa de linguagem visual esportiva.
- [web.dev: Animations and performance](https://web.dev/articles/animations-and-performance): animações de transform e opacity, sem loop de rolagem ou biblioteca de animação.
- Naviê: superfícies translúcidas, bordas iluminadas, hierarquia e espaçamento, conforme `padroes-navie-painel.md`.

## Implementação

`trail.css` é carregado após estilos específicos para uniformizar site, conta, checkout, story e páginas do evento. Inter nos títulos, Poppins no texto. A barra de navegação usa blur 14 px (10 px no celular); cartões usam reflexo e transparência pintados, sem multiplicar filtros. O painel e a autenticação compartilham a mesma paleta com seus próprios estilos.

Marcas de pneu são um SVG decorativo local; seis partículas CSS animam apenas opacity/transform. IntersectionObserver realiza entradas uma vez e suspende partículas fora da tela. A aba oculta pausa animações; `prefers-reduced-motion` elimina movimentos. Não há botão de pausa, conforme solicitado. A faixa tipográfica tem entrada finita, não rolagem contínua.

Logo original e favicon originais foram preservados byte a byte. Variantes de exibição transparentes: `logo-nav.webp`, `logo-display.webp`, `favicon-64.png`. QR e campos hospedados de cartão mantêm a superfície necessária à leitura.

## Arte da abertura

Arte ilustrativa original gerada com a ferramenta integrada ImageGen, não é registro de participantes nem fotografia do prêmio. Arquivos finais: `frontend/static/img/trail-hero.webp` (1536×1024, 273426 bytes) e `trail-hero-mobile.webp` (768×512, 94124 bytes). O PNG de origem permanece no diretório de imagens geradas do Codex. Apenas redimensionamento e codificação WebP foram aplicados para entrega.

Prompt utilizado:

> Use case: ads-marketing. Asset type: wide editorial hero background for a Brazilian off-road motorcycle event website, 1536x1024 landscape. Create an original cinematic action photograph illustration: a helmeted motocross rider in charcoal and burnt-orange protective gear on an unbranded orange dirt bike, taking a dramatic dirt trail bend and spraying red clay dust, rugged green Serra da Ibiapaba foothills vaguely silhouetted in background, warm late-afternoon rim light. Rider and bike prominent in the RIGHT HALF (right two-thirds), front wheel near lower right; LEFT THIRD deep charcoal atmospheric negative space for real HTML headline. Palette near-black, rust earth, fiery orange and subtle red, silver highlights, saturated but premium editorial color. Detailed real knobby tires, particles frozen in air, actual physically plausible bike anatomy. Ground tire tracks lead from bottom towards rider. Dynamic diagonal composition, tactile dust and cinematic contrast, avoid sci-fi. No text, letters, numbers, logos, badges, sponsor brands, watermarks, UI, cards or graphic borders. Image is atmosphere for the event, not documentation of actual participants. Do not incorporate or alter any existing team logo.

## Sorteio

Módulo independente, em preparação: apresentação e configuração por evento, interesses opcionais com consentimento. Não emite, reserva ou vende cotas, nem gera cobranças ou lançamentos financeiros. Dados e regras aguardam as transcrições do organizador e a ativação posterior do fluxo apropriado. Não confundir esse rascunho com checkout pronto.
