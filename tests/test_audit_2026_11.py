"""Regressões da auditoria de novembro/2026."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.events.models import MotorcycleRaffle
from apps.finance.models import FinancialEntry, FinancialEntryKind
from apps.identity.models import EventMembership, RoleChoices, User
from apps.identity.views_team import _script_json
from apps.payments.services import _ledger_split
from apps.registrations.reset_service import reset_test_registrations
from tests import test_system as _fixtures


class AuditRegressionTests(TestCase):
    def setUp(self):
        _fixtures.SistemaTrilhasTests.setUp(self)

    def paid_order(self, **extra):
        return Order.objects.create(event=self.event, merchant_reference=extra.pop("ref", "TM-REAL-1"), purchaser_name="Comprador",
            purchaser_email="c@example.test", status=OrderStatus.PAID, gross_cents=12000, total_cents=12000,
            expires_at=timezone.now() + timedelta(minutes=30), **extra)

    def test_reset_refuses_when_real_paid_order_exists(self):
        self.paid_order()
        with self.assertRaisesMessage(ValidationError, "TM-REAL-1"):
            reset_test_registrations(event=self.event)
        self.assertTrue(Order.objects.filter(merchant_reference="TM-REAL-1").exists())

    def test_reset_requires_an_event(self):
        with self.assertRaises(ValidationError):
            reset_test_registrations(event=None)

    def test_event_manager_cannot_set_password_of_existing_account(self):
        manager = User.objects.create_user(email="gerente@example.test", password="Example-test-2026", full_name="Gerente")
        EventMembership.objects.create(user=manager, event=self.event, role_code=RoleChoices.EVENT_MANAGER)
        victim = User.objects.create_user(email="participante@example.test", password="Original-pass-2026", full_name="Participante")
        self.client.force_login(manager)
        self.client.post(reverse("gestao:team_management", args=[self.event.pk]), {
            "email": victim.email, "full_name": "Participante", "password": "Tomada-de-conta-2026", "role_preset": "CREDENCIAMENTO"})
        victim.refresh_from_db()
        self.assertTrue(victim.check_password("Original-pass-2026"))
        self.assertFalse(EventMembership.objects.filter(user=victim, event=self.event).exists())

    def test_empty_custom_role_has_no_finance_access(self):
        from apps.core.permissions import can_manage_event
        user = User.objects.create_user(email="vazio@example.test", password="Example-test-2026")
        EventMembership.objects.create(user=user, event=self.event, role_code=RoleChoices.CUSTOM,
                                       permissions_json={"finance": [], "registrations": []})
        self.assertFalse(can_manage_event(user, self.event, "finance"))
        self.assertFalse(can_manage_event(user, self.event, "registrations"))

    def test_team_json_cannot_close_script_tag(self):
        self.assertNotIn("</script>", _script_json({"name": "</script><script>alert(1)</script>"}))

    def test_payment_ledger_is_split_between_registration_and_raffle(self):
        raffle = MotorcycleRaffle.objects.create(event=self.event)
        order = self.paid_order(ref="TM-MIX-1")
        OrderLine.objects.create(order=order, quantity=1, unit_cents=15000, net_cents=15000, item_snapshot_json={})
        OrderLine.objects.create(order=order, raffle=raffle, quantity=5, unit_cents=1600, net_cents=8000, item_snapshot_json={})
        _ledger_split(order, "123", 23000, "REVENUE", "Pagamento")
        rows = dict(FinancialEntry.objects.filter(order=order).values_list("kind", "amount_cents_signed"))
        self.assertEqual(rows, {FinancialEntryKind.RECEITA_INSCRICAO: 15000, FinancialEntryKind.RECEITA_SORTEIO: 8000})
        _ledger_split(order, "123", -23000, "REFUND-23000", "Devolução", refund=True)
        self.assertEqual(sum(FinancialEntry.objects.filter(order=order).values_list("amount_cents_signed", flat=True)), 0)

    def test_analytics_restricted_to_administrators(self):
        member = User.objects.create_user(email="portaria@example.test", password="Example-test-2026")
        EventMembership.objects.create(user=member, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.client.force_login(member)
        self.assertEqual(self.client.get(reverse("gestao:analytics")).status_code, 403)
