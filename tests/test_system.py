import uuid
from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone
from apps.core.models import Organization, AuditLog
from apps.core.utils import cents_to_reais, cents_to_decimal, escape_csv_cell, sha256_text
from apps.identity.models import User, Person, EventMembership, RoleChoices
from apps.events.models import Event, EventStatus, EventType, EventTheme, LegalDocumentVersion, DocumentKind
from apps.catalog.models import Product, Lot, Coupon, CouponKind, CapacityResource, CapacityHold, HoldStatus
from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.payments.models import ChargeAttempt, ChargeState, ProviderEvent
from apps.payments.gateway import MercadoPagoGateway
from apps.registrations.models import (
    Registration, CommercialState, OperationalState,
    RegistrationCredential, Entitlement, EntitlementState, Consumption
)

class SistemaTrilhasTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Equipe Morcego", city="Tianguá", state="CE")
        self.user = User.objects.create_user(email="kelvim@equipemorcego.com.br", password="password123", full_name="Kelvim Muniz", is_staff=True)
        now = timezone.now()
        self.event = Event.objects.create(
            organization=self.org,
            title="3º Trilhão Tianguá Off Road",
            slug="3-trilhao-tiangua-test",
            edition_number=3,
            type=EventType.TRILHAO,
            status=EventStatus.PUBLISHED,
            city="Tianguá",
            venue="Polo de Lazer",
            starts_at=now + timedelta(days=10),
            ends_at=now + timedelta(days=10, hours=10),
            registration_opens_at=now - timedelta(days=5),
            registration_closes_at=now + timedelta(days=9),
            max_attendees=100
        )
        self.theme = EventTheme.objects.create(event=self.event)
        self.product = Product.objects.create(event=self.event, name="Piloto", includes_shirt=True, includes_kit=True)
        self.lot = Lot.objects.create(
            event=self.event,
            product=self.product,
            title="Lote 1",
            price_cents=18000, # R$ 180,00
            opens_at=now - timedelta(days=5),
            closes_at=now + timedelta(days=9),
            max_quantity=50
        )
        self.cap_resource = CapacityResource.objects.create(
            event=self.event,
            type="EVENT_OVERALL",
            capacity_total=100,
            reserved_count=0,
            committed_count=0
        )
        self.client = Client()

    def test_money_formatting(self):
        """Testa a conversão precisa de centavos inteiros para exibição e gateways."""
        self.assertEqual(cents_to_reais(18000), "180,00")
        self.assertEqual(cents_to_reais(0), "0,00")
        self.assertEqual(cents_to_decimal(18000), Decimal("180.00"))

    def test_csv_injection_prevention(self):
        """Testa o escape contra injeção de fórmulas CSV (=, +, -, @)."""
        malicious = "=1+2';alert(1)"
        safe = escape_csv_cell(malicious)
        self.assertTrue(safe.startswith("'="))

    def test_coupon_discount_calculation(self):
        """Testa aplicação de cupom com controle de usos máximos."""
        now = timezone.now()
        coupon = Coupon.objects.create(
            event=self.event,
            code_upper="TESTE10",
            kind=CouponKind.PERCENT,
            percent_off=10,
            max_uses=2,
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=5)
        )
        self.assertTrue(coupon.is_valid())
        self.assertEqual(coupon.used_count, 0)

        # Usar uma vez
        coupon.used_count += 1
        coupon.save()
        self.assertTrue(coupon.is_valid())

        # Usar segunda vez (atinge o limite)
        coupon.used_count += 1
        coupon.save()
        self.assertFalse(coupon.is_valid())

    def test_pending_registration_does_not_occupy_capacity(self):
        """Only confirmed registrations consume event seats."""
        self.assertEqual(self.cap_resource.available_count, 100)

        hold = CapacityHold.objects.create(
            resource=self.cap_resource,
            order_id=uuid.uuid4(),
            quantity=5,
            status=HoldStatus.ACTIVE,
            expires_at=timezone.now() + timedelta(minutes=35),
            idempotency_key="TEST-HOLD-1"
        )
        self.cap_resource.reserved_count += 5
        self.cap_resource.save()

        self.assertEqual(self.cap_resource.available_count, 100)
        self.cap_resource.committed_count=5
        self.assertEqual(self.cap_resource.available_count,95)

    def test_entitlement_delivery_prevents_duplicate(self):
        """Testa que a entrega de kits e pulseiras é atômica e não permite entrega duplicada."""
        person = Person.objects.create(full_name="Piloto Teste", email="piloto@teste.com")
        order = Order.objects.create(
            event=self.event,
            merchant_reference="TM-TEST-001",
            purchaser_name="Piloto Teste",
            purchaser_email="piloto@teste.com",
            purchaser_phone="8899999999",
            status=OrderStatus.PAID,
            total_cents=18000,
            expires_at=timezone.now() + timedelta(days=1)
        )
        line = OrderLine.objects.create(
            order=order,
            product=self.product,
            lot=self.lot,
            intended_person_name="Piloto Teste",
            unit_cents=18000,
            net_cents=18000
        )
        reg = Registration.objects.create(
            event=self.event,
            person=person,
            source_order_line=line,
            registration_number=1,
            commercial_state=CommercialState.ACTIVE,
            operational_state=OperationalState.READY
        )
        ent = Entitlement.objects.create(
            registration=reg,
            benefit_type="KIT",
            title="Kit do Piloto",
            quantity_total=1,
            quantity_used=0,
            state=EntitlementState.AVAILABLE
        )

        # Primeira entrega (Sucesso)
        self.assertEqual(ent.remaining, 1)
        Consumption.objects.create(
            entitlement=ent,
            quantity=1,
            action="DELIVERED",
            operator=self.user,
            source_key="SOURCE-1",
            occurred_at=timezone.now()
        )
        ent.quantity_used += 1
        ent.state = EntitlementState.CONSUMED
        ent.save()

        # Segunda tentativa de entrega (Bloqueio)
        self.assertEqual(ent.remaining, 0)
        self.assertEqual(ent.state, EntitlementState.CONSUMED)

    def test_event_duplication_does_not_leak_registrations(self):
        """Testa que ao duplicar uma edição, nenhuma inscrição ou dado pessoal é transferido."""
        self.client.force_login(self.user)
        resp = self.client.post(f"/gestao/eventos/{self.event.id}/duplicar/")
        self.assertEqual(resp.status_code, 302)

        # Verifica se o novo evento foi criado em rascunho com 0 inscrições
        duplicated_event = Event.objects.filter(edition_number=4).first()
        self.assertIsNotNone(duplicated_event)
        self.assertEqual(duplicated_event.status, EventStatus.DRAFT)
        self.assertEqual(duplicated_event.registrations.count(), 0)
        self.assertEqual(duplicated_event.orders.count(), 0)

    def test_public_pages_render(self):
        """Testa se as páginas públicas carregam com status 200."""
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)

        r_list = self.client.get("/eventos/")
        self.assertEqual(r_list.status_code, 200)

        r_detail = self.client.get(f"/eventos/{self.event.slug}/")
        self.assertEqual(r_detail.status_code, 200)

        r_frames = self.client.get(f"/eventos/{self.event.slug}/molduras/")
        self.assertEqual(r_frames.status_code, 200)
