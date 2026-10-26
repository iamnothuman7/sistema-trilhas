# Temas e controle das inscrições — 26/10/2026

O site, a área do participante, o checkout, a autenticação e a gestão compartilham agora os temas claro e escuro. O padrão continua escuro; a escolha do visitante fica salva no navegador. A camada `theme.css` centraliza cores, superfícies translúcidas, barras de rolagem, foco e ajustes para telas pequenas. O hero fotográfico mantém contraste escuro nos dois temas, e o QR Code e os campos hospedados do cartão preservam o fundo branco necessário à leitura.

Cada participante precisa informar seu próprio CPF válido, mesmo em compras em grupo. O banco impede inscrições reservadas ou ativas repetidas para o mesmo CPF no mesmo evento. Reservas expiradas liberam o CPF. Pagamentos tardios de inscrições canceladas exigem revisão, sem reativação automática. A migração aproveita apenas documentos válidos do participante e nunca deduz sua identidade pelo CPF do comprador.

A arte de piloto confirmado exige uma inscrição ativa de piloto com pedido pago, acessível ao comprador ou ao titular vinculado. A página e as APIs de recorte, armazenamento e download verificam essa condição. A montagem pertence à conta que a criou e à inscrição utilizada.

Validação realizada:

- 361 testes Django aprovados, incluindo CPF, pagamento, permissões e arte.
- 31 testes JavaScript aprovados, incluindo persistência, alternância e acessibilidade do tema.
- Verificação de migrações e diferenças de código sem erros.
- 32 páginas públicas e administrativas renderizadas na homologação, com os controles e arquivos de tema presentes.
- Pré-verificação da produção sem CPFs válidos duplicados. Há uma inscrição legada sem CPF válido, que não pode ser identificada automaticamente.

A inspeção visual interativa no celular e desktop permanece pendente: a ferramenta de navegação falhou ao inicializar, inclusive após reinício do aplicativo. A renderização das páginas e os testes automatizados não substituem essa inspeção.
