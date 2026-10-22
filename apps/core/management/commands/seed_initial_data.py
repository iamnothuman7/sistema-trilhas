import secrets
import qrcode
import io
import base64
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.core.models import Organization
from apps.core.utils import sha256_text
from apps.identity.models import User, Person, UserPersonLink, EventMembership, RoleChoices
from apps.events.models import Event, EventStatus, EventType, EventTheme, Route, RouteCheckpoint, Sponsor, SponsorTier, LegalDocumentVersion, DocumentKind
from apps.catalog.models import Product, ProductKind, Lot, ProductVariant, Coupon, CouponKind, CapacityResource
from apps.checkout.models import Order, OrderLine, OrderStatus, ReceivableDue, DueType
from apps.payments.models import ChargeAttempt, ChargeState, PaymentMethod
from apps.registrations.models import (
    Registration, CommercialState, OperationalState,
    RegistrationCredential, Entitlement, EntitlementState, TermsAcceptance
)
from apps.finance.models import FinancialEntry, FinancialEntryKind, Expense, Vendor

class Command(BaseCommand):
    help = "Popula o banco com os dados iniciais completos da Equipe Morcego e do 3º Trilhão Tianguá"

    def handle(self, *args, **options):
        self.stdout.write("Iniciando população de dados da Equipe Morcego...")

        # 1. Organização
        org, _ = Organization.objects.get_or_create(
            name="Equipe Morcego",
            defaults={
                "legal_name": "Equipe Morcego Off-Road Tianguá",
                "city": "Tianguá",
                "state": "CE",
                "support_email": "contato@equipemorcego.com.br",
                "phone": "(88) 99999-9999"
            }
        )

        # 2. Usuário Administrador / Kelvim Muniz
        admin_user, created_user = User.objects.get_or_create(
            email="kelvim@equipemorcego.com.br",
            defaults={
                "full_name": "Kelvim Muniz",
                "phone": "(88) 99999-9999",
                "is_staff": True,
                "is_superuser": True,
            }
        )
        if created_user:
            admin_user.set_password("morcego2026!")
            admin_user.save()
            self.stdout.write(self.style.SUCCESS("Usuário Kelvim Muniz criado (kelvim@equipemorcego.com.br / morcego2026!)"))

        # Papel Global OWNER
        EventMembership.objects.get_or_create(
            user=admin_user,
            event=None,
            role_code=RoleChoices.OWNER
        )

        # 3. Evento Oficial: 3º Trilhão Tianguá Off Road
        now = timezone.now()
        event, _ = Event.objects.get_or_create(
            slug="3-trilhao-tiangua-off-road",
            defaults={
                "organization": org,
                "title": "3º Trilhão Tianguá Off Road",
                "edition_number": 3,
                "type": EventType.TRILHAO,
                "status": EventStatus.PUBLISHED,
                "city": "Tianguá",
                "state": "CE",
                "venue": "Polo de Lazer de Tianguá",
                "timezone": "America/Fortaleza",
                "starts_at": now + timedelta(days=25, hours=7),
                "ends_at": now + timedelta(days=25, hours=18),
                "registration_opens_at": now - timedelta(days=10),
                "registration_closes_at": now + timedelta(days=23),
                "max_attendees": 300,
                "description": "O 3º Trilhão Tianguá Off Road é o evento mais aguardado pelos amantes das duas rodas na Serra da Ibiapaba! Venha desbravar trilhas técnicas, single tracks, paredões de pedra e paisagens incríveis com café da manhã reforçado, pontos de hidratação, troféus pesados para equipes e o tradicional churrasco com música ao vivo na chegada.",
                "rules_summary": "Obrigatório o uso de capacete com óculos, botas, calça de proteção e luvas. Proibido pilotar sob efeito de álcool. Siga as orientações dos fiscais e equipes de resgate.",
                "contact_email": "contato@equipemorcego.com.br",
                "contact_phone": "(88) 99999-9999"
            }
        )

        # Tema do Evento
        theme, _ = EventTheme.objects.get_or_create(
            event=event,
            defaults={
                "primary_color": "#F59E0B",
                "secondary_color": "#10B981",
                "surface_color": "#111827",
            }
        )

        # Recurso de Capacidade Geral
        CapacityResource.objects.get_or_create(
            event=event,
            type="EVENT_OVERALL",
            defaults={"capacity_total": 300}
        )

        # 4. Produtos e Lotes
        prod_piloto, _ = Product.objects.get_or_create(
            event=event,
            name="Inscrição Piloto Completo",
            defaults={
                "kind": ProductKind.PILOTO,
                "description": "Pacote completo com direito à trilha, camiseta oficial personalizada, café da manhã, almoço com churrasco, pulseira de acesso e troféu.",
                "active": True,
                "includes_shirt": True,
                "includes_meal": True,
                "includes_kit": True,
            }
        )

        # Variantes de Camiseta
        for size in ["P", "M", "G", "GG", "XG"]:
            ProductVariant.objects.get_or_create(
                product=prod_piloto,
                kind="SHIRT_SIZE",
                code=size,
                defaults={"label": f"Camiseta Tamanho {size}", "extra_price_cents": 0}
            )

        # Lotes
        Lot.objects.get_or_create(
            event=event,
            product=prod_piloto,
            title="1º Lote Promocional",
            defaults={
                "price_cents": 18000, # R$ 180,00
                "opens_at": now - timedelta(days=10),
                "closes_at": now + timedelta(days=15),
                "max_quantity": 150,
                "priority": 1,
                "active": True
            }
        )
        Lot.objects.get_or_create(
            event=event,
            product=prod_piloto,
            title="2º Lote Geral",
            defaults={
                "price_cents": 20000, # R$ 200,00
                "opens_at": now + timedelta(days=15),
                "closes_at": now + timedelta(days=23),
                "max_quantity": 150,
                "priority": 2,
                "active": True
            }
        )

        # Produto Acompanhante
        prod_acomp, _ = Product.objects.get_or_create(
            event=event,
            name="Acompanhante / Garupa",
            defaults={
                "kind": ProductKind.ACOMPANHANTE,
                "description": "Acesso à concentração, café da manhã, almoço com churrasco e pulseira do evento.",
                "active": True,
                "includes_shirt": False,
                "includes_meal": True,
                "includes_kit": True,
            }
        )
        Lot.objects.get_or_create(
            event=event,
            product=prod_acomp,
            title="Lote Único Acompanhante",
            defaults={
                "price_cents": 6000, # R$ 60,00
                "opens_at": now - timedelta(days=10),
                "closes_at": now + timedelta(days=23),
                "max_quantity": 100,
                "priority": 1,
                "active": True
            }
        )

        # 5. Cupons
        Coupon.objects.get_or_create(
            event=event,
            code_upper="MORCEGO10",
            defaults={
                "kind": CouponKind.PERCENT,
                "percent_off": 10,
                "max_uses": 50,
                "valid_from": now - timedelta(days=5),
                "valid_until": now + timedelta(days=20),
                "active": True
            }
        )
        Coupon.objects.get_or_create(
            event=event,
            code_upper="TIANGUA20",
            defaults={
                "kind": CouponKind.FIXED,
                "value_cents": 2000, # R$ 20,00
                "max_uses": 30,
                "valid_from": now - timedelta(days=5),
                "valid_until": now + timedelta(days=20),
                "active": True
            }
        )

        # 6. Documentos Legais Versionados
        doc_reg, _ = LegalDocumentVersion.objects.get_or_create(
            event=event,
            kind=DocumentKind.REGULAMENTO,
            version_number=1,
            defaults={
                "content": """REGULAMENTO OFICIAL - 3º TRILHÃO TIANGUÁ OFF ROAD
1. O evento é aberto a pilotos amadores e experientes que estejam munidos de todos os equipamentos de segurança.
2. É expressamente obrigatório o uso de capacete integral, botas para off-road, calça e luvas durante toda a permanência no percurso.
3. Não haverá cronometragem oficial competitiva nem pódio por tempo; o evento preza pelo companheirismo, espírito esportivo e superação dos obstáculos.
4. Os participantes devem respeitar as propriedades rurais, manter as cancelas fechadas e não descartar lixo na natureza.
5. Em caso de quebra ou necessidade de auxílio, aguarde nos pontos sinalizados ou acione o veículo de apoio mais próximo."""
            }
        )

        doc_termo, _ = LegalDocumentVersion.objects.get_or_create(
            event=event,
            kind=DocumentKind.TERMO_RESPONSABILIDADE,
            version_number=1,
            defaults={
                "content": """TERMO DE RESPONSABILIDADE E ISENÇÃO DE RISCOS
Declaro para os devidos fins que participo por livre e espontânea vontade do 3º Trilhão Tianguá Off Road, assumindo integralmente a responsabilidade por meus atos e condições de saúde.
Estou ciente dos riscos inerentes ao esporte off-road e isento a organização da Equipe Morcego, coordenadores e patrocinadores de quaisquer responsabilidades por danos materiais ou físicos decorrentes de imperícia ou acidentes durante o evento."""
            }
        )

        # 7. Trilha e Rota
        route, _ = Route.objects.get_or_create(
            event=event,
            title="Trilha da Serra e Despenhadeiro - 65 km",
            defaults={
                "distance_km": 65.0,
                "difficulty": "Média / Difícil",
                "description": "Percurso desenhado na borda da Serra da Ibiapaba, com descidas técnicas em pedra, trechos rápidos de estradão e lamaçal nos vales.",
                "public_flag": True
            }
        )
        RouteCheckpoint.objects.get_or_create(route=route, code="CP-01", defaults={"label": "Concentração Polo de Lazer", "sort_order": 1})
        RouteCheckpoint.objects.get_or_create(route=route, code="CP-02", defaults={"label": "Ponto de Apoio Mirante da Serra", "sort_order": 2})
        RouteCheckpoint.objects.get_or_create(route=route, code="CP-03", defaults={"label": "Ponto de Hidratação Cachoeira", "sort_order": 3})
        RouteCheckpoint.objects.get_or_create(route=route, code="CP-04", defaults={"label": "Chegada & Pódio", "sort_order": 4})

        # 8. Patrocinadores
        Sponsor.objects.get_or_create(
            event=event,
            name="Posto Serra Branca",
            defaults={"tier": SponsorTier.OURO, "website": "https://equipemorcego.com.br"}
        )
        Sponsor.objects.get_or_create(
            event=event,
            name="Motopeças & Oficina Tianguá",
            defaults={"tier": SponsorTier.OURO, "website": "https://equipemorcego.com.br"}
        )
        Sponsor.objects.get_or_create(
            event=event,
            name="Churrascaria & Restaurante Ibiapaba",
            defaults={"tier": SponsorTier.PRATA, "website": "https://equipemorcego.com.br"}
        )

        # 9. Inscrição Piloto Demo para Testes Imediatos do Scanner e Credencial
        demo_person, _ = Person.objects.get_or_create(
            full_name="Kelvim Muniz",
            defaults={
                "email": "kelvim@equipemorcego.com.br",
                "phone": "(88) 99999-9999",
                "city": "Tianguá",
                "state": "CE",
                "team_name": "Equipe Morcego"
            }
        )
        UserPersonLink.objects.get_or_create(user=admin_user, person=demo_person)

        demo_order, _ = Order.objects.get_or_create(
            merchant_reference="TM-03-DEMO101",
            defaults={
                "event": event,
                "purchaser_user": admin_user,
                "purchaser_name": "Kelvim Muniz",
                "purchaser_email": "kelvim@equipemorcego.com.br",
                "purchaser_phone": "(88) 99999-9999",
                "purchaser_document": "123.456.789-00",
                "status": OrderStatus.PAID,
                "gross_cents": 18000,
                "discount_cents": 0,
                "total_cents": 18000,
                "expires_at": now + timedelta(days=30),
            }
        )

        demo_line, _ = OrderLine.objects.get_or_create(
            order=demo_order,
            intended_person_name="Kelvim Muniz",
            defaults={
                "product": prod_piloto,
                "intended_person_email": "kelvim@equipemorcego.com.br",
                "intended_person_phone": "(88) 99999-9999",
                "intended_person_city": "Tianguá",
                "intended_person_state": "CE",
                "intended_person_team": "Equipe Morcego",
                "intended_variant_code": "G",
                "quantity": 1,
                "unit_cents": 18000,
                "net_cents": 18000,
                "item_snapshot_json": {
                    "product_name": prod_piloto.name,
                    "price_cents": 18000,
                    "shirt_size": "G"
                }
            }
        )

        demo_reg, _ = Registration.objects.get_or_create(
            source_order_line=demo_line,
            defaults={
                "event": event,
                "person": demo_person,
                "registration_number": 101,
                "category_name": "Piloto Completo",
                "team_name": "Equipe Morcego",
                "shirt_size": "G",
                "commercial_state": CommercialState.ACTIVE,
                "operational_state": OperationalState.READY,
                "product_snapshot_json": demo_line.item_snapshot_json,
            }
        )

        # Termos aceitos para o piloto demo
        TermsAcceptance.objects.get_or_create(
            registration=demo_reg,
            document_version=doc_reg,
            accepting_user=admin_user,
            defaults={"evidence_hash": sha256_text("demo_terms_accepted_reg")}
        )
        TermsAcceptance.objects.get_or_create(
            registration=demo_reg,
            document_version=doc_termo,
            accepting_user=admin_user,
            defaults={"evidence_hash": sha256_text("demo_terms_accepted_termo")}
        )

        # Credencial e QR Code Demo
        raw_token = "MORCEGO101"
        qr_img = qrcode.make(f"TRILHA:{event.slug}:{raw_token}")
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        b64_qr = base64.b64encode(buf.getvalue()).decode("utf-8")

        RegistrationCredential.objects.get_or_create(
            registration=demo_reg,
            defaults={
                "token_digest": sha256_text(raw_token),
                "raw_token": raw_token,
                "qr_code_image": b64_qr,
            }
        )

        # Entitlements
        Entitlement.objects.get_or_create(
            registration=demo_reg,
            benefit_type="KIT",
            defaults={"title": "Kit Oficial do Piloto (Sacola/Adesivos)", "quantity_total": 1}
        )
        Entitlement.objects.get_or_create(
            registration=demo_reg,
            benefit_type="CAMISETA",
            defaults={"title": "Camiseta Oficial (Tamanho G)", "quantity_total": 1}
        )
        Entitlement.objects.get_or_create(
            registration=demo_reg,
            benefit_type="REFEICAO",
            defaults={"title": "Café da Manhã & Almoço com Churrasco", "quantity_total": 1}
        )
        Entitlement.objects.get_or_create(
            registration=demo_reg,
            benefit_type="ACESSO_PISTA",
            defaults={"title": "Pulseira de Acesso à Trilha", "quantity_total": 1}
        )

        # Financeiro Demo
        FinancialEntry.objects.get_or_create(
            event=event,
            source_reference=f"ORDER-{demo_order.merchant_reference}",
            kind=FinancialEntryKind.RECEITA_INSCRICAO,
            defaults={
                "order": demo_order,
                "amount_cents_signed": 18000,
                "description": "Recebimento Inscrição Piloto #101 Kelvim Muniz",
                "recognized_at": now
            }
        )

        # Despesa Demo
        vendor, _ = Vendor.objects.get_or_create(
            organization=org,
            name="Confecção de Camisetas do Nordeste",
            defaults={"category": "Kits e Camisetas", "pix_key": "confeccao@email.com"}
        )
        Expense.objects.get_or_create(
            event=event,
            description="Entrada de 50% na Confecção de 300 Camisetas",
            defaults={
                "vendor": vendor,
                "category": "Kits_CAMISETAS",
                "amount_cents": 450000, # R$ 4.500,00
                "due_at": now.date(),
                "paid_at": now.date(),
                "status": "PAID"
            }
        )

        self.stdout.write(self.style.SUCCESS("População inicial concluída com 100% de sucesso!"))
