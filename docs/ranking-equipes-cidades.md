# Ranking de equipes, cidades e artes internas

O ranking da edição conta **inscrições**, não pessoas distintas: cada `Registration` entra uma vez quando está `ACTIVE` e o pedido de origem está `PAID`, ambos na mesma edição. A situação operacional (`NOT_READY`, `READY` ou credenciamento) não impede a contagem comercial. Reservas, cancelamentos, transferências e pedidos expirados, reembolsados ou ainda pendentes ficam de fora.

`apps.events.rankings.get_event_rankings(event, limit=10)` recalcula os dados ao ser chamado. Retorna `teams`, `cities`, `confirmed_count`, `team_group_count`, `city_group_count`, `team_registration_count` e `city_registration_count`. As linhas contêm nome, UF, posição e quantidade. `limit=None` oferece a lista completa para a gestão; o padrão entrega os dez primeiros grupos, mantendo os totais completos. Empates compartilham a posição e aparecem em ordem alfabética. Nenhum participante, telefone, e-mail ou documento integra o resultado.

As equipes usam `Registration.team_name`; as cidades usam `Person.city` e `Person.state`. A chave de comparação remove acentos, diferenças de caixa e espaços, preservando pontuação. Não existe aproximação fonética nem união automática de grafias parecidas. Nomes vazios, “não informado”, “sem equipe”, “sem cidade” e “-” ficam fora dos grupos. Uma cidade sem UF é um grupo separado das cidades com UF informada.

## Correções administrativas

A tela `gestao:ranking_management`, por edição, exige a capacidade `registrations`. `TeamAlias` corrige nomes de equipe; `CityAlias` corrige nomes de cidade somente dentro da UF escolhida. As correções não alteram inscrições, pessoas, pagamentos ou dados históricos. A migração `events.0006_ranking_aliases` cria somente essas duas tabelas, após `0005_sponsor_logo_preview`.

Salvar novamente o mesmo nome de origem atualiza sua correção. Salvar o mesmo conteúdo não cria outra correção nem outro registro de auditoria. É possível corrigir apenas a apresentação (“SAOJOAO” → “São João”) ou grafias distintas. Uma sequência explícita de correções leva ao nome final; ciclos são recusados. Remover uma correção exige POST e refaz o agrupamento usando as regras restantes. Gravações e remoções usam CSRF, escopo de evento, transação, bloqueio do evento e auditoria atômica; falha na auditoria desfaz a alteração.

O partial público `events/_ranking.html` recebe `ranking` e mostra somente os agregados. O endpoint GET `events:ranking_snapshot` usa o slug exato e as mesmas regras de publicação/preview da edição, com `Cache-Control: no-store`. `rankings.js` consulta esse resumo a cada minuto somente com a página e a seção visíveis, preserva os últimos totais em caso de falha e não insere nomes como HTML. Não há publicação de participantes individuais.

## Artes de equipes confirmadas

`gestao:team_frame_editor` é uma ferramenta interna com capacidade `content`, em `/gestao/eventos/<event_id>/molduras/equipes/`. Usa o mesmo agrupamento e as mesmas correções do ranking. Não oferece confirmação manual de uma equipe sem inscrição ativa e paga.

A foto e a logo da equipe são abertas localmente pelo navegador. O editor aceita PNG, JPG ou WebP de até 15 MB e 24 megapixels, preserva proporção/transparência e exporta PNG de 1080 × 1920 (story) ou 1080 × 1350 (feed). A mensagem complementar admite 70 caracteres; o nome da equipe vem da inscrição/correção, com limite de 120. O gestor pode ajustar enquadramento, zoom e posição da foto, além da posição da logo.

A identidade da edição prioriza a logo configurada no evento. A arte original de 2027 é fallback somente para o slug exato `3-trilhao-tiangua-off-road-2027`; outras edições usam o emblema original Morcego. Nenhuma logo é recortada ou redesenhada.

O Canvas gera o download por Blob no dispositivo. Não há POST de fotos, backup, `FrameTemplate`, `GeneratedMontage`, acesso público nem migração para o editor. A página autenticada é enviada com `Cache-Control: no-store`. O gerador público existente continua separado.

## Verificação

- `python manage.py test tests.test_rankings tests.test_team_frames --noinput` valida escopo, autorização, grupos pagos/ativos, alias, UF, ciclos, rollback de auditoria, ausência de dados individuais e separação entre editor interno e público.
- `node --test tests/team_frame_editor.test.cjs` executa as funções reais do renderizador para enquadramento, texto longo, story/feed, proporção de logos e nome do download.
- A QA de navegador deve confirmar exportação PNG real, seleção de arquivo, controles no celular e legibilidade da composição. O teste Node usa um contexto de desenho controlado e não substitui essa inspeção.
