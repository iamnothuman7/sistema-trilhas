from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order
from apps.events.models import MotorcycleRaffle, RaffleSalesPoint, RaffleSellerSettlement, RaffleTicket
from apps.events.raffle_results import eligible_tickets
from apps.events.raffle_sales import (parse_packages, price_cents, record_seller_sale, record_settlement,
                                      seller_summaries, void_seller_sale)
from apps.finance.models import FinancialEntry
from apps.identity.models import EventMembership, RoleChoices, User
from tests import test_system as _fixtures


class RaffleSellerTests(TestCase):
    def setUp(self):
        _fixtures.SistemaTrilhasTests.setUp(self)
        self.raffle = MotorcycleRaffle.objects.create(
            event=self.event, is_public=True, motorcycle_model="MXF 270", motorcycle_count=2,
            quota_price_cents=2000, package_prices=[{"qty": 5, "cents": 8000}, {"qty": 10, "cents": 15000}],
            rules_text="Regulamento do sorteio.", authorization_reference="SPA 01/2026",
            draw_at=timezone.now() + timedelta(days=20))
        self.seller = RaffleSalesPoint.objects.create(raffle=self.raffle, name="João Moto Peças")
        self.admin = User.objects.create_user(email="gestor.sorteio@example.test", password="Example-test-2026", full_name="Gestor")
        EventMembership.objects.create(user=self.admin, event=self.event, role_code=RoleChoices.EVENT_MANAGER)

    def test_price_uses_cheapest_combination_of_packages(self):
        self.assertEqual(price_cents(self.raffle, 1), 2000)
        self.assertEqual(price_cents(self.raffle, 5), 8000)
        self.assertEqual(price_cents(self.raffle, 7), 12000)
        self.assertEqual(price_cents(self.raffle, 10), 15000)
        self.assertEqual(price_cents(self.raffle, 16), 15000 + 8000 + 2000)
        self.assertEqual(parse_packages("5=80,00; 10=150,00"), [{"qty": 5, "cents": 8000}, {"qty": 10, "cents": 15000}])
        with self.assertRaises(ValidationError):
            parse_packages("cinco por oitenta")

    def test_seller_sale_issues_sequential_numbers_that_enter_the_draw(self):
        reference, numbers, total = record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Maria Silva",
            buyer_phone="(88) 99999-0000", quantity=5, actor=self.admin)
        self.assertEqual(numbers, [1, 2, 3, 4, 5])
        self.assertEqual(total, 8000)
        tickets = RaffleTicket.objects.filter(sale_reference=reference)
        self.assertEqual(sum(t.amount_cents for t in tickets), 8000)
        self.assertEqual(eligible_tickets(self.raffle).count(), 5)
        _, second, _ = record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Pedro", buyer_phone="",
            quantity=2, actor=self.admin)
        self.assertEqual(second, [6, 7])
        # A venda presencial não entra no caixa até o repasse.
        self.assertFalse(FinancialEntry.objects.filter(raffle=self.raffle).exists())

    def test_sale_blocked_without_authorization_or_rules(self):
        self.raffle.authorization_reference = ""
        self.raffle.save()
        with self.assertRaisesMessage(ValidationError, "autorização"):
            record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Maria", buyer_phone="", quantity=1, actor=self.admin)
        self.assertFalse(RaffleTicket.objects.exists())

    def test_void_removes_numbers_from_draw_and_pending_balance(self):
        reference, _, _ = record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Maria", buyer_phone="", quantity=5, actor=self.admin)
        void_seller_sale(raffle=self.raffle, reference=reference, reason="Comprador desistiu da compra", actor=self.admin)
        self.assertEqual(eligible_tickets(self.raffle).count(), 0)
        summary = seller_summaries(self.raffle)[0]
        self.assertEqual((summary.numbers, summary.sold_cents, summary.pending_cents), (0, 0, 0))
        _, next_numbers, _ = record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Ana", buyer_phone="", quantity=1, actor=self.admin)
        self.assertEqual(next_numbers, [6])

    def test_settlement_posts_cash_once_and_reduces_pending(self):
        record_seller_sale(raffle=self.raffle, seller=self.seller, buyer_name="Maria", buyer_phone="", quantity=10, actor=self.admin)
        today = timezone.localdate()
        settlement, created = record_settlement(seller=self.seller, amount_cents=10000, received_on=today, method="PIX", notes="",
                                                request_key="abc123", actor=self.admin)
        again, created_again = record_settlement(seller=self.seller, amount_cents=10000, received_on=today, method="PIX", notes="",
                                                 request_key="abc123", actor=self.admin)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(settlement.pk, again.pk)
        self.assertEqual(FinancialEntry.objects.filter(raffle=self.raffle).count(), 1)
        summary = seller_summaries(self.raffle)[0]
        self.assertEqual((summary.sold_cents, summary.settled_cents, summary.pending_cents), (15000, 10000, 5000))

    def test_management_pages_render_and_register_sale(self):
        self.client.force_login(self.admin)
        for name in ("raffle_overview", "raffle_sellers", "raffle_management", "raffle_operations", "raffle_result"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f"gestao:{name}", args=[self.event.pk])).status_code, 200)
        response = self.client.post(reverse("gestao:raffle_sellers", args=[self.event.pk]), {
            "action": "sale", "seller": self.seller.pk, "buyer_name": "Carlos Souza", "buyer_phone": "(88) 98888-7777", "quantity": "5"}, follow=True)
        self.assertContains(response, "Venda registrada")
        self.assertContains(response, "wa.me/5588988887777")
        detail = self.client.get(reverse("gestao:raffle_seller_detail", args=[self.event.pk, self.seller.pk]))
        self.assertContains(detail, "Carlos Souza")
        self.assertContains(detail, "80,00")

    def test_site_purchase_closed_creates_no_order(self):
        response = self.client.post(reverse("events:raffle_buy", args=[self.event.slug]), {
            "quantity": "5", "buyer_name": "Visitante", "buyer_phone": "88999990000", "buyer_email": "v@example.test", "rules_accepted": "on"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.assertContains(self.client.get(reverse("events:raffle_buy", args=[self.event.slug])), "Venda pelo site fechada")

    def test_site_purchase_without_registration_is_eligible_after_payment(self):
        from apps.checkout.models import OrderLine, OrderStatus
        from apps.events.raffle_operations import activate_purchased_numbers
        order = Order.objects.create(event=self.event, merchant_reference="SO-TEST-1", purchaser_name="Visitante", purchaser_email="v@example.test",
                                     status=OrderStatus.PAID, gross_cents=8000, total_cents=8000, expires_at=timezone.now() + timedelta(minutes=30))
        OrderLine.objects.create(order=order, raffle=self.raffle, quantity=5, unit_cents=1600, net_cents=8000, item_snapshot_json={})
        activate_purchased_numbers(order)
        tickets = eligible_tickets(self.raffle)
        self.assertEqual(tickets.count(), 5)
        self.assertEqual(sum(t.amount_cents for t in tickets), 8000)
