# Credenciamento e retirada de kits no celular

Abra **Gestão → selecione a edição → Credenciamento** com uma conta autorizada a operar credenciamento nessa edição. A página mostra dados operacionais do participante; não compartilhe a conta ou a tela com terceiros.

## Ler ou localizar a inscrição

1. Toque em **Iniciar câmera**, permita o acesso e aponte para o QR da credencial ou do comprovante. O leitor prefere a câmera traseira. **Trocar câmera** alterna entre as câmeras disponíveis; **Parar** libera a câmera.
2. A leitura para a câmera e abre a ficha. Ela não confirma presença nem registra retirada automaticamente.
3. Se a câmera não funcionar, abra **Usar uma foto do QR** para fotografar/escolher uma imagem ou busque pelo número (`42` ou `#42`), nome ou código do QR. A foto é processada no dispositivo, sem upload ao servidor. São aceitos JPG, PNG e WebP de até 15 MB e 24 megapixels; a verificação de resolução ocorre depois que o navegador abre a imagem.
4. Quando houver nomes iguais ou busca ampla, selecione explicitamente a inscrição conferindo número, nome e cidade. A busca retorna no máximo dez opções e pede refinamento se houver mais. Ela sempre fica restrita à edição selecionada.

Confira a identidade com o participante antes de agir. A busca manual tem as mesmas restrições da leitura de QR: não libera pagamento pendente, credencial revogada/expirada ou documentos sem aceite válido do titular.

## Confirmar presença e entregar materiais

- **Confirmar presença** registra o check-in uma única vez. Presença e entrega de material são registros independentes.
- Cada benefício mostra quantidade entregue e restante. Escolha uma quantidade inteira dentro do saldo (até 1.000 por operação) e confirme a entrega daquele item. Entregas parciais continuam disponíveis pelo saldo restante.
- A ficha mostra até vinte movimentações recentes, com quantidade, item, horário na zona do evento e operador. Os registros anteriores continuam preservados no banco.
- A retirada exige inscrição `ACTIVE`, pedido `PAID`, estado `READY` ou `CHECKED_IN`, credencial existente/válida e todos os documentos atualmente vigentes aceitos por usuário vinculado ao titular. Eventos cancelados/arquivados bloqueiam operações; eventos encerrados podem permitir retirada se todos os demais requisitos continuarem válidos.
- O saldo e o histórico são atualizados na mesma transação, com bloqueio da inscrição e do benefício. Uma chave única identifica cada intenção de entrega. Repetir a mesma intenção não consome novo saldo; mudar a quantidade dessa mesma intenção é recusado.

## Se a conexão falhar

Não há operação offline. Se a resposta de uma entrega não chegar, mantenha a aba aberta e use **Conferir entrega pendente** ou tente novamente: a mesma chave e quantidade serão reutilizadas. Esses identificadores ficam no `sessionStorage` da aba para sobreviver à recarga; nomes, documentos e imagens não são armazenados ali. Fechar a aba ou limpar o armazenamento pode perder a intenção local: consulte o histórico e o saldo antes de iniciar outra entrega. Uma recusa explícita do servidor permite corrigir a quantidade; uma resposta incerta preserva a chave original.

Não entregue material confiando apenas no clique ou na animação: confira o saldo atualizado e o histórico. Se a sessão expirar, entre novamente com uma conta autorizada.

## Diagnóstico da câmera

- Acesso à câmera depende de HTTPS e permissão do navegador. Abra o endereço oficial diretamente no Safari/Chrome, especialmente se estiver dentro do navegador embutido de WhatsApp/Instagram.
- Se a permissão foi negada, libere **Câmera** nas permissões do site e toque em **Iniciar câmera** novamente. Se estiver ocupada, feche o aplicativo que a utiliza.
- Use boa iluminação, QR inteiro na tela e sem reflexos. Fotos muito grandes podem exigir memória demais no aparelho; prefira enquadrar apenas a credencial.
- A câmera é liberada ao parar, sair da página, ocultar a aba ou concluir a leitura. Uma permissão concedida depois de tocar em parar não reabre a câmera.

O scanner usa `BarcodeDetector` quando o navegador oferece leitura QR e cai para a biblioteca local **jsQR 1.4.0** quando necessário. Isso corrige a dependência anterior exclusiva de uma API que não está disponível em todos os navegadores. Processamento de vídeo limitado a um frame por aproximadamente 300 ms, com maior dimensão de 900 px; fotos usam até 1.600 px. Não há dependência de CDN para decodificar.

Referências primárias: [jsQR e licença Apache 2.0](https://github.com/cozmo/jsQR), [disponibilidade de BarcodeDetector](https://developer.mozilla.org/en-US/docs/Web/API/BarcodeDetector), [requisitos e erros de getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## Validação desta entrega

`tests.test_scanner_operations` + `tests.test_management_access`: 28 testes passaram em banco SQLite de teste isolado. `node --test tests/scanner_ui.test.cjs`: 17 testes passaram, incluindo decodificação de uma matriz QR real, câmera sem API nativa, recusa de permissão, cancelamento de permissão pendente, troca/encerramento de tracks, foto local, respostas fora de ordem, homônimos e repetição segura de entrega.

Os testes simulam as APIs de câmera; não certificam foco/lente/permissões em hardware iOS ou Android. Antes do evento, faça um ensaio no celular que será usado, com inscrições de homologação e material de teste. Concorrência foi protegida por locks/transação no código e coberta por replay/rollback; esta rodada não realizou teste de carga com banco PostgreSQL ou celulares simultâneos.
