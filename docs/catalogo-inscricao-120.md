# Inscrição de piloto a R$ 120

O inventário de produção em 22/10/2026 encontrou o evento `3-trilhao-tiangua-off-road-2027`, dois lotes de piloto (R$ 180 atual e R$ 200 futuro), acompanhante de R$ 60 e um pedido pago de R$ 180. Nenhuma alteração foi aplicada durante esse inventário.

O comando abaixo mostra o plano sem gravar alterações:

```console
python manage.py configure_registration_120 --event 3-trilhao-tiangua-off-road-2027
```

Depois da revisão do plano, `--apply` define R$ 120 nos lotes ativos de piloto ainda não encerrados e desativa os produtos de outras modalidades desse evento para novas inscrições. Não cria nem exclui produtos ou lotes. O slug precisa corresponder exatamente ao evento.

```console
python manage.py configure_registration_120 --event 3-trilhao-tiangua-off-road-2027 --apply
```

A aplicação usa transação e bloqueio do evento compartilhado com a criação de pedidos. Registra a mudança na auditoria; uma nova execução sem mudanças não gera uma segunda auditoria. O catálogo é mutável, mas valores, status e snapshots de pedidos/linhas existentes permanecem intactos, inclusive pedidos pendentes. Não altera cobranças, cotas, inscrições ou contadores de capacidade.

Página pública e checkout consultam os mesmos lotes disponíveis. O servidor valida produto, evento, lote ativo, datas e capacidade; os valores enviados pelo navegador não definem o preço. Novos pedidos usam o preço do catálogo no momento da criação. Descontos válidos existentes continuam sendo calculados no servidor.

O anúncio do sorteio aparece apenas na edição com publicação autorizada. Cotas ficam em breve, sem venda, reserva de número ou pagamento. Não foi criada integração financeira para o sorteio.
