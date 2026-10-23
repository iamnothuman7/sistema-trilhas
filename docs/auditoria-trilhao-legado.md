# Auditoria do site legado e dos assets do Trilhão

Auditoria de 22/10/2026. Fonte pública: [site legado](https://ramilos-comunicacoes.github.io/trilhao/) e [repositório](https://github.com/ramilos-comunicacoes/trilhao), revisão `9dfd7c11102c4d607b8b83c2f3f9b4aca35b54a7` de 01/04/2026. A cópia de referência está em `references/trilhao-legado/`; nenhum script do projeto foi executado.

O material mistura edições. A página principal e o README identificam a segunda edição; existe um anúncio separado da terceira. Fotografias inspecionadas mostram também a primeira edição, em 2025. Por isso, a legenda correta para o conjunto é **“Edições anteriores”**.

## Logo e anúncio corretos de 2027

| Arquivo atual | Características verificadas | Uso adequado |
| --- | --- | --- |
| `frontend/static/img/logo_3trilhao_2027.png` | 590 × 370, RGBA, 279.651 bytes, transparência real; texto “3º Trilhão Tianguá Off Road 2027” | Marca específica da edição, com `object-fit: contain`, sem moldura/filtro/fundo acrescentado |
| `frontend/static/img/badge_3_trilhao_2027.png` | 590 × 370, RGB, 345.905 bytes; contém cenário | Composição ilustrada; não substituir a logo transparente |
| `frontend/static/img/cartaz_trilhao_2027.jpg` | 819 × 1024, 417.694 bytes | Cartaz com data de 20 e 21 de março de 2027 |
| `references/trilhao-legado/assets/images/edicao3.png` | 3375 × 4219, RGB, 9.786.661 bytes | Fonte de alta resolução do mesmo anúncio; evitar download integral no carregamento da homepage |

SHA256 da logo transparente: `3bc1237bdfc0a6651293d27660fc7921d973e1d6fd6e83b7f5673fdb93fa80bc`. As cópias em `media/events/logos/` e `references/qa-media/events/logos/` são idênticas. Nenhuma delas foi alterada nesta auditoria.

O nome de arquivo não basta para identificar a arte: `assets/images/logo.png` do legado diz **2º / 2026**, e `logo-grande.png` anuncia uma **CRF 250F** com referências à edição de 2026. Nenhuma dessas duas imagens deve representar a marca ou o prêmio de 2027. O emblema da Equipe Morcego continua sendo a marca da organização, distinta da marca da edição.

## Fotos preparadas para uso

Foram copiadas seis fotografias públicas, sem recompressão ou alteração de pixels, para `frontend/static/img/historico/`. Os originais permanecem no clone de referência; os hashes de origem/destino conferem. O manifesto completo está em `references/trilhao-site/copied-assets.json`.

| Arquivo | Dimensões | Bytes | Descrição visual para legenda/alt |
| --- | --- | ---: | --- |
| `trilhao-foto-01.jpg` | 1064 × 1600 | 378.296 | Pilotos reunidos em uma estrada cercada por vegetação |
| `trilhao-foto-02.jpg` | 1600 × 1064 | 318.515 | Grupo de participantes diante do painel do encontro |
| `trilhao-foto-03.jpg` | 1600 × 1066 | 250.900 | Participantes levantando troféus |
| `trilhao-foto-04.jpg` | 1064 × 1600 | 301.749 | Concentração de pilotos e motocicletas |
| `trilhao-foto-05.jpg` | 1600 × 1066 | 325.919 | Participantes reunidos ao lado de uma motocicleta |
| `trilhao-foto-06.jpg` | 1064 × 1600 | 280.176 | Pilotos em uma concentração do evento |

As fotos 02, 03, 05 e 06 contêm referências visíveis a “1º Trilhão” e/ou 2025. Não associar essas fotos à terceira edição ou apresentá-las como confirmação de patrocinadores/prêmios atuais. Preservar o conteúdo original e mostrar a identificação histórica no bloco da galeria. Usar dimensões explícitas, carregamento adiado abaixo da primeira dobra e visualização ampliada acessível, com fechamento por Escape e retorno de foco.

Por autorização posterior, foram gerados três derivados WebP para entrega web, com largura de 800 px, qualidade 82 e redimensionamento Lanczos. Os JPEGs permanecem byte a byte intactos; a codificação foi repetida e comparada para confirmar saída determinística.

| Derivado em `frontend/static/img/historico/` | Dimensões | Bytes |
| --- | --- | ---: |
| `trilhao-foto-01.webp` | 800 × 1203 | 220.254 |
| `trilhao-foto-02.webp` | 800 × 532 | 94.480 |
| `trilhao-foto-03.webp` | 800 × 533 | 69.764 |

As páginas `core/about.html` e `core/contact.html` foram atualizadas com esse acervo, legendas históricas, álbuns externos e contatos encontrados. Telefone/e-mail atuais da organização têm prioridade; valores padrão fictícios conhecidos usam o contato público como alternativa. Os GETs `/sobre/` e `/contato/` passaram com SQLite de QA aberto somente para leitura: resposta 200, links/arquivos existentes, IDs únicos e prioridade de contatos configurados verificada.

## Links e contatos encontrados

O HTML de origem foi preservado em `references/trilhao-site/index.html`; o inventário está em `public-links-assets.json` na mesma pasta.

| Destino | Endereço da fonte | Verificação |
| --- | --- | --- |
| Álbum no Google Drive | [Pasta pública](https://drive.google.com/drive/folders/1GOzd5u8kr_xl-rZQ8YBopfnd-AKuVEkI) | HTTP 200 em 22/10/2026; conteúdo do álbum não foi importado |
| Álbum Banlek | [Álbum 1e1d6d](https://banlek.com/album/1e1d6d) | HTTP 200; “LINK 2” e “LINK 3” apontam ao mesmo endereço, usar um único botão |
| Instagram da organização | [Equipe Morcego das Trilhas](https://www.instagram.com/equipemorcegodastrilhas/) | Endereço presente no rodapé; não confundir com perfis dos patrocinadores |
| WhatsApp principal | [Contato da organização](https://wa.me/5588992874581) | Corresponde ao telefone público (88) 99287-4581 |
| E-mail | `tianguaoffroad@gmail.com` | Texto de contato no rodapé |
| WhatsApp secundário legado | `https://wa.me/558892507312` | Corresponde a um telefone local de oito dígitos; não completar um dígito automaticamente |

Fontes no clone: `index.html:1196` (álbuns), `index.html:1213` (anúncio da próxima edição), `index.html:1224` (contatos), `index.html:1225` (redes) e `index.html:1232` (WhatsApp secundário). Respostas HTTP 200 confirmam disponibilidade naquele momento, não propriedade ou atualização editorial do conteúdo externo. Nenhuma mensagem foi enviada.

## O que aproveitar e o que manter histórico

- Aproveitar os acessos claros a evento, galeria e contato; o destaque da edição; a hierarquia das cotas dos patrocinadores; fotografias reais e identidade regional. Integrar ao sistema existente, com seus componentes, ícones e controles de acesso.
- Galeria e vídeos podem formar um acervo histórico. Os dois MP4 originais têm aproximadamente 8,27 MB e 2,15 MB; permaneceram apenas na referência. Se utilizados, oferecer reprodução voluntária com poster e `preload="none"`.
- Rankings antigos são tabelas estáticas. Para 2027, qualquer ranking precisa vir de inscrições elegíveis da própria edição, com regra explícita de contagem; não transformar a tabela legada em dados operacionais.
- Métricas, percurso, premiação, marcas e cotas do passado não comprovam o programa de 2027. A confirmação dos patrocinadores atuais deve continuar vindo da gestão do evento. Há links provisórios `patrocinador*.com` e `#` no material antigo, que não são contatos válidos para importar.

O repositório possui 57 arquivos de assets, somando 138.795.765 bytes. Alguns PNGs individuais têm entre 9 e 20 MB. Copiar toda essa entrega para a homepage reintroduziria o problema de desempenho. O legado também inicializa partículas globais e animações contínuas; mantém vídeos em reprodução automática e depende de bibliotecas externas. Preservar os efeitos leves e o respeito a movimento reduzido da implementação atual, sem reintroduzir o botão de pausar removido a pedido do usuário.

## Preparação da próxima publicação

Baseline confirmado em leitura de produção: `2799b026f2c25fac752fe9d82a1e2dd1c41a1ebd`. Os helpers ignorados pelo Git foram preparados para a próxima publicação autorizada; não foram executados para publicar ou alterar produção nesta auditoria.

1. `references/_audit_tools/deploy_release.py` usa `2799b026...` como baseline. O servidor exige o marcador dessa revisão e hashes das fontes antes de interromper o serviço. Arquivos do ambiente e banco continuam fora do pacote.
2. `apply_release_server.py` exige SQLite no caminho esperado e `events/0005_sponsor_logo_preview` antes do stop e novamente na referência de migrações do backup. O rollback dinâmico preserva todo o conjunto anterior, inclusive `events0004` e `events0005`; somente migrações introduzidas na nova release podem ser revertidas antes do start.
3. O sinal `started=True` continua anterior à tentativa de iniciar o serviço. Depois dessa tentativa, uma falha de healthcheck preserva banco/código para diagnóstico; não restaura o SQLite a partir de snapshot. A checagem Django anterior ao start inclui home, login, agenda, sobre, contato e subdomínio do evento.
4. `counts-before-2799b026.json` registra apenas contagens e somas agregadas obtidas com conexão somente leitura: um pedido pago de R$ 180,00, uma inscrição, um lançamento, uma despesa, três patrocinadores, um anúncio de sorteio e zero interesses. Nenhum nome, contato, credencial ou segredo foi coletado. A verificação compara também as somas das linhas, pedidos, razão e despesas antes e depois dos GETs.
5. `verify_release.py` verifica os hashes HTTP de CSS/JS, fontes e ícones locais, novos heroes, logo 2027 e três WebPs históricos; preserva separadamente os hashes originais dos PNGs. Confere home sem blocos de sorteio ou patrocinadores, mantendo esses conteúdos na página do evento. Não exige apagar o anúncio ou os patrocinadores do banco.
6. Após publicar o código, executar `verify_release.py` sem argumentos verifica o estado sem exigir alteração de preços. Após a execução autorizada do comando `configure_registration_120 --event 3-trilhao-tiangua-off-road-2027 --apply`, executar `verify_release.py --offer-120` também exige lotes de piloto atuais/futuros por 12.000 centavos, ausência de outras modalidades ativas e oferta pública de R$ 120,00. Ambos preservam e verificam o pedido histórico de R$ 180,00. O verificador não executa esse comando nem qualquer POST.
7. Antes da publicação, finalizar a suíte integrada e `makemigrations --check`; se surgir uma migração além de `events0005`, conferir seu plano em SQLite descartável. Não recriar o anúncio existente na migração. Os CSS/JS alterados devem ter versão de cache atualizada, pois estáticos possuem cache de 30 dias.

Validação dos helpers nesta rodada: sintaxe dos três arquivos e dos códigos Python enviados ao servidor aprovada; os mesmos 15 GETs de gestão e 7 públicos do verificador passaram no SQLite de QA aberto com `mode=ro` e `PRAGMA query_only=ON`. O rollback sem migrações novas preservou `events0005`; uma baseline inexistente foi rejeitada antes de gravações. Os 72 nomes literais de ícones usados nos templates existem na biblioteca Lucide vendorizada. As seis fontes WOFF2 locais têm cabeçalhos e tamanhos íntegros, somam 107.948 bytes, usam `font-display: swap` e possuem suas licenças no repositório. Essas verificações locais não substituem os healthchecks após a publicação.

Não houve modificação em home, CSS, views, banco local, mídia de produção ou servidor nesta auditoria. As mudanças na aplicação ficaram nos assets históricos e nas duas páginas institucionais autorizadas acima; os demais arquivos produzidos estão em `references/` ou nesta documentação.
