# 🦇 EQUIPE MORCEGO — SISTEMA DE GESTÃO DE TRILHÕES E EXPEDIÇÕES OFF-ROAD

Plataforma profissional de gestão de eventos off-road, inscrições online, conciliação Mercado Pago, credenciamento rápido com QR Code e gerador de molduras digitais.

Desenvolvido para **Kelvim Muniz** e a **Equipe Morcego** (Tianguá - Ceará).

---

## ⚡ Principais Funcionalidades

### 1. Portal Público & Microsites por Evento
* **Identidade Visual Independente:** Cores, banners, cronograma, percurso e patrocinadores customizados por edição.
* **Catálogo & Lotes:** Preços em centavos inteiros, lotes com controle de capacidade transacional (`select_for_update`), evitando *oversell*.
* **Compras em Grupo:** Um comprador pode pagar inscrições para N pilotos com dados e camisetas individuais.

### 2. Checkout Transparente & Mercado Pago (Orders API)
* **Pix Instantâneo:** Gera QR Code em imagem e código Copia e Cola com identificação automática via Webhook em tempo real.
* **Cartão de Crédito:** Suporte a tokenização segura e parcelamento.
* **Webhooks Seguros:** Validação criptográfica de assinatura **HMAC SHA-256** e deduplicação estrita de eventos (`ProviderEvent`).

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

### 7. Expedições de Múltiplos Dias
* Controle de etapas diárias, alocação de pousadas/quartos e veículos de transporte com controle de capacidade por leito.

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

> **Credenciais de Administrador (Kelvim Muniz):**
> * **E-mail:** `kelvim@equipemorcego.com.br`
> * **Senha:** `morcego2026!`

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
