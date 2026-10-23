# Identidade de trilha — outubro de 2026

Direção aprovada pelo proprietário: somente tema escuro, terra, laranja e vermelho; logo original sem texto HTML ao lado no cabeçalho público; navegação em cápsula com vidro; referências de pneus e trilha; movimento automático sem botão de pausa.

## Referências consultadas

- [Awwwards: Lando Norris](https://www.awwwards.com/sites/lando-norris): identidade esportiva expressiva e interação ligada ao esporte. A paleta da referência não foi copiada.
- [Awwwards: Sports](https://www.awwwards.com/websites/sports/): pesquisa de linguagem visual esportiva.
- [web.dev: Animations and performance](https://web.dev/articles/animations-and-performance): animações de transform e opacity, sem loop de rolagem ou biblioteca de animação.
- Naviê: superfícies translúcidas, bordas iluminadas, hierarquia e espaçamento, conforme `padroes-navie-painel.md`.

## Implementação

`trail.css` é carregado após estilos específicos para uniformizar site, conta, checkout, story e páginas do evento. Inter nos títulos, Poppins no texto. A barra de navegação usa blur 14 px (10 px no celular); cartões usam reflexo e transparência pintados, sem multiplicar filtros. O painel e a autenticação compartilham a mesma paleta com seus próprios estilos.

Marcas de pneu são um SVG decorativo local; seis partículas CSS animam apenas opacity/transform. IntersectionObserver realiza entradas uma vez e suspende partículas fora da tela. A aba oculta pausa animações; `prefers-reduced-motion` elimina movimentos. Não há botão de pausa, conforme solicitado. Na revisão institucional, a faixa tipográfica passou a ter rolagem contínua enquanto visível.

Logo original e favicon originais foram preservados byte a byte. Variantes de exibição transparentes: `logo-nav.webp`, `logo-display.webp`, `favicon-64.png`. QR e campos hospedados de cartão mantêm a superfície necessária à leitura.

## Arte anterior da abertura

Arte ilustrativa original gerada com a ferramenta integrada ImageGen, não é registro de participantes nem fotografia do prêmio. Arquivos anteriores, substituídos na revisão institucional abaixo: `frontend/static/img/trail-hero.webp` (1536×1024, 273426 bytes) e `trail-hero-mobile.webp` (768×512, 94124 bytes). O PNG de origem permanece no diretório de imagens geradas do Codex. Apenas redimensionamento e codificação WebP foram aplicados para entrega.

Prompt utilizado:

> Use case: ads-marketing. Asset type: wide editorial hero background for a Brazilian off-road motorcycle event website, 1536x1024 landscape. Create an original cinematic action photograph illustration: a helmeted motocross rider in charcoal and burnt-orange protective gear on an unbranded orange dirt bike, taking a dramatic dirt trail bend and spraying red clay dust, rugged green Serra da Ibiapaba foothills vaguely silhouetted in background, warm late-afternoon rim light. Rider and bike prominent in the RIGHT HALF (right two-thirds), front wheel near lower right; LEFT THIRD deep charcoal atmospheric negative space for real HTML headline. Palette near-black, rust earth, fiery orange and subtle red, silver highlights, saturated but premium editorial color. Detailed real knobby tires, particles frozen in air, actual physically plausible bike anatomy. Ground tire tracks lead from bottom towards rider. Dynamic diagonal composition, tactile dust and cinematic contrast, avoid sci-fi. No text, letters, numbers, logos, badges, sponsor brands, watermarks, UI, cards or graphic borders. Image is atmosphere for the event, not documentation of actual participants. Do not incorporate or alter any existing team logo.

## Sorteio

Módulo independente, em preparação: apresentação e configuração por evento, interesses opcionais com consentimento. Não emite, reserva ou vende cotas, nem gera cobranças ou lançamentos financeiros. Dados e regras aguardam as transcrições do organizador e a ativação posterior do fluxo apropriado. Não confundir esse rascunho com checkout pronto.

## Revisão institucional e mobile — 22/10/2026

A entrada agora reúne identidade da Equipe Morcego, calendário real, apresentação da equipe e memória das edições anteriores. Patrocinadores e anúncio do sorteio aparecem apenas na edição correspondente. O calendário prioriza encontros em andamento e exclui rascunhos/cancelados; eventos com data encerrada deixam o destaque.

Foram reaproveitados a logo transparente específica do 3º Trilhão 2027, fotos históricas, galerias e contatos verificados do site anterior. Veja a proveniência e as diferenças entre as edições em [auditoria-trilhao-legado.md](auditoria-trilhao-legado.md). A logo original não foi redesenhada; a variante WebP tem 590×370 e 27402 bytes.

O movimento usa câmera suave na arte, entradas tipográficas, faixa contínua, partículas de terra, rastros de pneu, linhas de velocidade, emblema flutuante, percurso SVG e reflexos/elevação dos cards. IntersectionObserver suspende cada seção fora da área visível; aba oculta e preferência do sistema por menos movimento são respeitadas. A contagem regressiva usa a data real da edição e atualiza por minuto. Nenhum botão de pausa foi adicionado.

As artes de desktop e celular foram geradas separadamente, não são fotografias do evento ou da moto do sorteio. A moto inteira foi composta abaixo da área reservada ao menu. O retrato usa proporção 2:3; no celular é exibido integralmente, com texto abaixo do veículo. A logo da edição ocupa o lado do texto para não encobrir a moto. Os arquivos originais gerados permanecem no diretório do ImageGen. Apenas redimensionamento e codificação WebP foram usados para entrega:

- Desktop: frontend/static/img/trail-hero-desktop-v2.webp, 1536×1024, 260436 bytes.
- Mobile: frontend/static/img/trail-hero-mobile-v2.webp, 768×1152, 123124 bytes.
- Ferramenta: ImageGen integrada, duas gerações novas, fundo opaco; sem substituição ou alteração das logos originais.

### Prompt desktop

Create a premium cinematic editorial action photograph for the Brazilian Equipe Morcego off-road motorcycle team's dark website. Landscape 1536x1024 composition. Authentic northeastern Brazil Serra da Ibiapaba highland trail, warm burnt orange clay, rugged scrub vegetation, dark dramatic charcoal sky, late afternoon amber rim light, flying earthy dust and tire tracks in foreground, energetic sharp sports photography. ONE fully visible motocross rider with full face helmet, black/charcoal gear with warm orange/red accents on an unbranded orange dirt motorcycle, performing a dynamic controlled turn. CRITICAL COMPOSITION: the entire rider AND entire motorcycle including BOTH wheels, helmet and handlebar must fit fully inside the RIGHT HALF of the image between 25% and 84% vertical height, with generous negative space around all edges. Rider head no higher than 25% from top. Upper 22% is entirely empty dark sky with NO rider or bike: reserved for a floating website navigation capsule. Left 48% is quiet dark atmospheric landscape for large white website text, no bike or distracting foreground objects. Bike must be large enough to be impactful in right half but never crop any parts. Camera at low angle, realistic anatomy and realistic dirt bike geometry, beautifully composed professional campaign art, rich detailed natural earth texture, deeply dark blacks without hiding rider silhouette, cinematic but believable. NO text, NO lettering, NO logos, NO numbers, NO watermark, NO graphic UI. Not a collage, no giant cropped wheel. The finished image must be suitable for a wide responsive web hero whose text is on the left. This is an illustrative image, no need to depict actual event attendees.

### Prompt mobile

Generate a vertical portrait 1024x1536 premium cinematic sports photograph composed SPECIFICALLY for a MOBILE off-road motorcycle website hero for Equipe Morcego in Tianguá Ceará Brazil. Create a NEW portrait composition. ONE fully visible realistic motocross rider with full face helmet and goggles, charcoal/black gear with warm red-orange accents on an unbranded burnt-orange dirt motorcycle, coming diagonally toward camera on an orange clay trail with tire tracks, highland scrub and Serra da Ibiapaba mountains in background. Dynamic earth and dust plume trailing to left, late-afternoon golden edge light, dark charcoal sky, premium authentic adventure campaign look. CRITICAL MOBILE COMPOSITION: all of rider, helmet, handlebars, full motorcycle and BOTH wheels entirely contained between x=14% and x=88% and y=23% and y=65% of canvas. NO cutoff anywhere, preserve lots of safe space. The top 22% must be quiet dark empty sky with NO subject, reserved for a floating capsule navigation. The bottom 30% must fade into deep charcoal earthy atmosphere with sparse softly visible ground only, reserved for large mobile headline overlaid in HTML. Rider and bike sharp with realistic engineering proportions and anatomy; head below the upper quarter, whole vehicle readable even on a small phone. Visually strong low front three-quarter perspective, no huge foreground wheel. Dark, warm, vivid cinematic orange dust, restrained realistic colors. NO writing, NO numbers, NO logos, NO watermark, NO UI. Do not create a landscape image or a cropped landscape: use portrait framing from the start.
