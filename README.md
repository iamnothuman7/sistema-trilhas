# 🦇 EQUIPE MORCEGO — SISTEMA DE GESTÃO DE TRILHÕES E EXPEDIÇÕES OFF-ROAD

Plataforma profissional de gestão de eventos off-road, inscrições online, conciliação Mercado Pago, credenciamento rápido com QR Code e gerador de molduras digitais.

Desenvolvido para **Kelvim Muniz** e a **Equipe Morcego** (Tianguá - Ceará).

---

## ⚡ Principais Funcionalidades

### 1. Portal Público & Microsites por Evento
* **Identidade Visual Independente:** Cores, banners, cronograma, percurso e patrocinadores customizados por edição.
* **Catálogo & Lotes:** Preços em centavos inteiros, lotes com controle de capacidade transacional (`select_for_update`), evitando *oversell*.
* **Compras em Grupo:** Um comprador pode pagar inscrições para N pilotos com dados e camisetas individuais.

### 2. Checkout Transparente & Mercado Pago (Payments API)
* **Pix Instantâneo:** Gera QR Code em imagem e código Copia e Cola com identificação automática via Webhook em tempo real.
* **Cartão de Crédito:** Suporte a tokenização segura e parcelamento.
* **Webhooks Seguros:** Validação criptográfica de assinatura **HMAC SHA-256** e deduplicação estrita de eventos (`ProviderEvent`).
* **Taxas e repasses:** configuração em `/gestao/integracoes/mercado-pago/taxas/`, com regras separadas por meio, tarifas por parcelas, simulação do líquido e cotação assinada antes da cobrança. As taxas públicas são referências editáveis; a conciliação usa as tarifas reais informadas pelo provedor.
* **Comissão opcional:** os parâmetros para outra conta ficam em rascunho, sem cobrança nem transferência. A ativação exige integrar e homologar o split 1:1 com autorização OAuth da conta vendedora; informar um ID não conecta o destino.

### 3. Área do Piloto & Segurança LGPD
* **Aceite Versionado de Regulamento:** Guarda o hash SHA-256 do documento, IP do participante e data/hora do aceite.
* **Credencial & QR Code:** Código opaco e seguro emitido apenas após confirmação comercial e aceite de termos.
* **Reivindicação de Inscrições:** Links seguros para pilotos de equipes reivindicarem suas inscrições compradas em lote.

### 4. Credenciamento & Scanner Mobile no Dia da Trilha
* **Leitura com Câmera ou Busca Rápida:** Funciona direto no navegador do celular do operador.
* **Controle Atômico de Entrega:** Registro independente de Presença (Check-in), Camiseta Oficial (com conferência de tamanho), Kit do Piloto e Refeição.
* **Proteção contra Dupla Entrega:** Bloqueio atômico com som de confirmação ou alerta de duplicidade.

### 5. Gerador Interativo de Molduras Digitais (Canvas 2D)
* **100% Client-Side:** O processamento da imagem acontece diretamente no navegador do piloto. **Nenhuma foto pessoal é enviada ao servidor**, garantindo total privacidade (LGPD).
* **Formatos Feed (1080x1350) e Story (1080x1920):** Zoom, rotação, enquadramento por toque/arraste e estampa automática do nome e equipe do piloto.

### 6. Financeiro Gerencial & Livro Razão
* **Livro Razão Imutável (Append-Only Ledger):** Registro detalhado de vendas brutas, recebimentos confirmados, estornos e despesas.
* **Apuração de Tarifas:** Separação entre tarifas estimadas e tarifas reais do Mercado Pago.
* **Exportação Segura:** Relatórios em CSV protegidos contra injeção de fórmulas (`=`, `+`, `-`, `@`).

### 7. Operação por Evento
* Contas individuais de colaboradores com permissões restritas à edição.
* Produtos extras com estoque próprio, disponíveis após inscrição paga e entregues pelo mesmo QR Code.
* Numeração após pagamento, bilhetes em papel com canhoto, pontos de venda e recebimentos integrados ao financeiro. A venda pública de cotas permanece indisponível.

---

## 🚀 Como Executar Localmente

### Pré-requisitos
* Python 3.11+
* Git

### Passo a Passo

1. **Ativar o Ambiente Virtual:**
```bash
# Windows
.\.venv\Scripts\activate

# Linux / Mac
source .venv/bin/activate
```

2. **Instalar Dependências:**
```bash
pip install -r requirements.txt
```

3. **Aplicar Migrações do Banco:**
```bash
python manage.py migrate
```

4. **Popular Dados Iniciais (Equipe Morcego + 3º Trilhão Tianguá):**
```bash
python manage.py seed_initial_data
```

5. **Iniciar o Servidor de Desenvolvimento:**
```bash
python manage.py runserver
```

Acesse:
* **Portal Público:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Painel de Gestão:** [http://127.0.0.1:8000/gestao/](http://127.0.0.1:8000/gestao/)
* **Área do Piloto:** [http://127.0.0.1:8000/minha-conta/](http://127.0.0.1:8000/minha-conta/)

> O primeiro administrador é criado com a senha informada em `MORCEGO_INITIAL_ADMIN_PASSWORD`. Não existe senha padrão. Um administrador existente mantém sua senha e suas permissões ao executar a carga inicial.

---

## 🧪 Testes Automatizados

Para rodar a suíte completa de testes de integridade, cálculo financeiro, concorrência e credenciamento:

```bash
python manage.py test tests
```

---

## 🐳 Implantação em Produção (Docker + Caddy + PostgreSQL)

Na VPS de produção:

1. Configure as variáveis em `infra/.env.example` para `.env`
2. Suba a stack completa com HTTPS automático gerenciado pelo Caddy:

```bash
cd infra
docker compose up -d --build
```

---

## 📁 Estrutura Modular da Aplicação

```
apps/
  ├── core/            # Configurações globais, Organização, Auditoria e Outbox
  ├── identity/        # Usuário customizado, Perfis, RBAC e LGPD
  ├── events/          # Gestão de Eventos, Temas, Cronograma, Percursos e Patrocinadores
  ├── catalog/         # Produtos, Lotes, Variantes de Camisetas, Cupons e Estoque
  ├── checkout/        # Cotações, Pedidos e Reservas Temporárias (Holds)
  ├── payments/        # Gateway Mercado Pago, Pix, Cartão e Webhooks
  ├── registrations/   # Inscrições, Termos Versionados, Credenciais e Entitlements
  ├── operations/      # Check-in, Credenciamento e Passagem em Pontos de Apoio
  ├── expeditions/     # Roteiros diários, Quartos de Hotéis e Transporte
  ├── mediaframes/     # Gerador de Molduras Digitais (Canvas 2D)
  ├── finance/         # Livro Razão, Despesas e Conciliação Financeira
  ├── reporting/       # KPIs e Exportações CSV protegidas
  └── communications/  # Fila de Notificações e Links de WhatsApp
frontend/
  ├── static/          # CSS moderno, JS do Scanner e do Editor de Moldura
  └── templates/       # Templates HTML responsivos (Mobile-First 360px)
infra/                 # Dockerfile, docker-compose.yml e Caddyfile
```

---
🦇 *Equipe Morcego Off-Road &bull; Tradição e Respeito nas Trilhas da Serra da Ibiapaba*


## Sorteio e operação financeira

A gestão do evento reúne apresentação, imagens, numeração, pontos e financeiro do sorteio. Fotos PNG/JPG/WebP de até 15 MB são validadas e convertidas para WebP; a transparência é preservada. Um pagamento confirmado de inscrição emite um único número incluído, sem receita adicional. A sequência digital pula a faixa reservada ao papel e nunca reutiliza números cancelados.

A venda online de números extras começa desativada. A organização preenche preço, limite online, data futura, regulamento, referências da autorização/enquadramento e da análise do Mercado Pago; confirma a conferência e marca a ativação. As referências são declaradas pela organização, não constituem validação jurídica automática. Os extras exigem inscrição paga, reutilizam a conta e o QR e usam o mesmo checkout, tarifas e webhook autenticado do evento. Reservas de extras controlam seu próprio estoque; números são emitidos somente após aprovação. Novas compras encerram 35 minutos antes do sorteio. Pedidos iniciados antes de desligar vendas preservam o preço e podem ser confirmados dentro do prazo.

Recebimentos, tarifas, devoluções e despesas vinculadas ao sorteio são filtrados no mesmo livro financeiro do evento, sem lançamentos duplicados. A devolução de extras invalida somente seus números; a inscrição e seu QR permanecem válidos. Para cancelar uma inscrição com extras pagos, registre primeiro a devolução desses pedidos. O registro manual de devolução pressupõe dinheiro já devolvido e não chama a API de reembolso do Mercado Pago.

As saídas aceitam comprovante privado PDF/JPG/PNG/WebP de até 15 MB, na criação, na quitação ou posteriormente. Correções e anulações preservam o lançamento original e registram compensações e auditoria. Documentos são baixados por uma rota com permissão financeira e escopo de evento, sem URL pública.

Inscrições aguardando pagamento não ocupam vagas do evento ou lote. Somente confirmações comprometem essas capacidades; a aprovação confere novamente o limite. Pagamento recebido quando já não há capacidade exige conciliação e não libera QR nem participação. A reserva de estoque dos produtos extras e números extras permanece separada das vagas de inscrição. O CPF continua limitado a uma inscrição ou pedido vigente por evento.

O cartão de credenciamento pode ser baixado em PDF ou PNG, com o mesmo QR revogável e os números confirmados. O aceite pessoal dos termos continua obrigatório para liberar a credencial; a organização não aprova manualmente inscrições pagas.

## Contas Mercado Pago e imagens dos eventos

Em Mercado Pago → Conexão, escolha renovar as chaves atuais ou adicionar outra conta/aplicação. Para substituir, informe Public Key, Access Token e assinatura do webhook da mesma aplicação; configure a URL e o evento Pagamentos no Mercado Pago e conclua a conexão. A conferência identifica o recebedor sem criar cobrança. Uma falha conserva a configuração ativa anterior. Homologação aceita apenas credenciais de teste, e produção aceita apenas credenciais reais.

Cada cobrança fica vinculada a uma configuração criptografada da conta que a criou. Ativar outra conta afeta novas cobranças; consultas e webhooks das anteriores continuam usando a configuração original. Preserve o acesso e o webhook antigos enquanto houver pagamentos a acompanhar. Renovar o token atualiza a mesma aplicação; trocar de conta não transfere saldo nem executa reembolsos. Somente proprietário global ou superusuário pode fazer essa alteração.

O banner institucional e sua prévia de compartilhamento são independentes das artes de cada evento. No editor de aparência, o banner muda o topo daquele evento, a capa 16:9 muda seu catálogo e compartilhamento, e a imagem 1:1 muda seu card na agenda. O resumo da compra de números mostra quantidade, preço unitário e subtotal. O PDF do globo inclui integralmente a seleção de números confirmados, com paginação e quantidade auditada.
