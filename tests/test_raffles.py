from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order
from apps.core.models import AuditLog
from apps.events.forms_raffles import MotorcycleRaffleForm
from apps.events.models import Event, EventStatus, MotorcycleRaffle, RaffleInterest
from apps.events.models_raffles import RAFFLE_INTEREST_CONSENT
from apps.finance.models import FinancialEntry
from apps.identity.models import EventMembership, RoleChoices, User
from apps.payments.models import ChargeAttempt
from apps.registrations.models import Registration
from tests import test_system as _fixtures


class MotorcycleRaffleTests(TestCase):
    def setUp(self):
        _fixtures.SistemaTrilhasTests.setUp(self)
        now = timezone.now()
        self.other = Event.objects.create(
            organization=self.org, title="Outra edição", slug="outra-edicao-raffle",
            starts_at=now, ends_at=now+timedelta(days=1),
            registration_opens_at=now, registration_closes_at=now+timedelta(hours=1),
            status=EventStatus.PUBLISHED,
        )

    def public_url(self, event=None):
        return reverse("events:raffle", kwargs={"slug": (event or self.event).slug})

    def interest_url(self, event=None):
        return reverse("events:raffle_interest", kwargs={"slug": (event or self.event).slug})

    def management_url(self, event=None):
        return reverse("gestao:raffle_management", kwargs={"event_id": (event or self.event).pk})

    def public_raffle(self, **changes):
        values = {"event": self.event, "is_public": True, "interest_enabled": True}
        values.update(changes)
        return MotorcycleRaffle.objects.create(**values)

    def manager(self, role=RoleChoices.EVENT_MANAGER):
        user = User.objects.create_user(email="raffle.manager@example.test", password="local-test-only", full_name="Gestor do sorteio")
        EventMembership.objects.create(user=user, event=self.event, role_code=role)
        self.client.force_login(user)
        return user

    def test_public_route_without_campaign_does_not_announce_sale_or_create_data(self):
        response = self.client.get(self.public_url())
        self.assertContains(response, "Nenhum sorteio divulgado para esta edição")
        self.assertNotContains(response, "R$ 0")
        self.assertFalse(MotorcycleRaffle.objects.exists())

    def test_unknown_event_slug_returns_404(self):
        self.assertEqual(self.client.get("/eventos/sem-evento/sorteio/").status_code, 404)

    def test_draft_event_is_hidden_from_visitors(self):
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=["status"])
        self.assertEqual(self.client.get(self.public_url()).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.public_url()).status_code, 200)

    def test_private_configuration_hidden_but_manager_can_preview(self):
        MotorcycleRaffle.objects.create(event=self.event, motorcycle_model="Modelo privado", quota_price_cents=1590)
        response = self.client.get(self.public_url())
        self.assertNotContains(response, "Modelo privado")
        self.assertNotContains(response, "15,90")
        self.manager()
        preview = self.client.get(self.public_url())
        self.assertContains(preview, "Modelo privado")
        self.assertContains(preview, "Prévia da organização")
        self.assertNotContains(preview, 'action="'+self.interest_url()+'"')

    def test_public_announcement_allows_undefined_details_without_fictitious_values(self):
        raffle = self.public_raffle(interest_enabled=False)
        response = self.client.get(self.public_url())
        self.assertContains(response, "A moto será apresentada em breve")
        self.assertContains(response, "Cotas em breve")
        self.assertContains(response, "Em breve")
        self.assertNotContains(response, "R$")
        self.assertNotContains(response, "None")
        self.assertFalse(raffle.purchase_available)

    def test_complete_configuration_never_enables_checkout(self):
        raffle = self.public_raffle(motorcycle_model="Moto informada", quota_price_cents=2590,
                                   quota_quantity=100, draw_at=timezone.now()+timedelta(days=20), rules_text="Regras informadas.")
        response = self.client.get(self.public_url())
        self.assertContains(response, "R$ 25,90")
        self.assertContains(response, "Regras informadas.")
        self.assertContains(response, 'disabled aria-disabled="true"')
        self.assertNotContains(response, "payment_method")
        self.assertFalse(raffle.purchase_available)

    def test_get_management_does_not_create_draft(self):
        self.manager()
        response = self.client.get(self.management_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MotorcycleRaffle.objects.exists())

    def test_manager_creates_draft_and_ignores_forged_event_or_sale_flags(self):
        self.manager()
        response = self.client.post(self.management_url(), {"title": "Sorteio da edição", "event": str(self.other.pk), "purchase_available": "true"})
        self.assertRedirects(response, self.management_url())
        raffle = MotorcycleRaffle.objects.get()
        self.assertEqual(raffle.event, self.event)
        self.assertFalse(raffle.is_public)
        self.assertFalse(raffle.interest_enabled)
        self.assertIsNone(raffle.quota_price_cents)
        self.assertIsNone(raffle.draw_at)
        self.assertFalse(raffle.purchase_available)
        self.assertTrue(AuditLog.objects.filter(action="RAFFLE_CREATED", event_id=self.event.pk).exists())

    def test_manager_updates_same_campaign_and_converts_price_to_cents(self):
        self.manager()
        raffle = self.public_raffle(interest_enabled=False)
        response = self.client.post(self.management_url(), {
            "title": "Título atualizado", "quota_price": "25,90", "quota_quantity": "100",
            "is_public": "on", "interest_enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        raffle.refresh_from_db()
        self.assertEqual(raffle.quota_price_cents, 2590)
        self.assertEqual(raffle.quota_quantity, 100)
        self.assertEqual(MotorcycleRaffle.objects.count(), 1)

    def test_invalid_values_or_private_interest_are_rejected(self):
        for values in ({"quota_price": "0"}, {"quota_price": "-1"}, {"quota_quantity": "0"}, {"quota_quantity": "-2"}, {"interest_enabled": "on"}):
            with self.subTest(values=values):
                form = MotorcycleRaffleForm({"title": "Rascunho", **values}, instance=MotorcycleRaffle(event=self.event))
                self.assertFalse(form.is_valid())

    def test_event_scope_and_role_are_enforced(self):
        self.manager()
        self.assertEqual(self.client.get(self.management_url(self.other)).status_code, 403)
        self.assertEqual(self.client.post(self.management_url(self.other), {"title": "Não alterar"}).status_code, 403)
        EventMembership.objects.filter(user=self.client.session["_auth_user_id"]).update(role_code=RoleChoices.CONTENT)
        self.assertEqual(self.client.get(self.management_url()).status_code, 403)
        self.assertFalse(MotorcycleRaffle.objects.exists())

    def test_audit_failure_rolls_back_configuration(self):
        self.manager()
        with patch("apps.events.views_raffles.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.management_url(), {"title": "Não persistir"})
        self.assertFalse(MotorcycleRaffle.objects.exists())

    def test_interest_requires_login_post_and_csrf(self):
        self.public_raffle()
        self.assertEqual(self.client.get(self.interest_url()).status_code, 405)
        self.assertEqual(self.client.post(self.interest_url(), {"consent": "on"}).status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(self.interest_url(), {"consent": "on"}).status_code, 403)
        self.assertFalse(RaffleInterest.objects.exists())

    def test_interest_requires_explicit_consent(self):
        self.public_raffle()
        self.client.force_login(self.user)
        response = self.client.post(self.interest_url(), {})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Confirme o aceite", status_code=400)
        self.assertFalse(RaffleInterest.objects.exists())

    def test_interest_is_idempotent_and_separate_from_orders_registrations_and_money(self):
        raffle = self.public_raffle()
        self.client.force_login(self.user)
        with patch("apps.payments.gateway.MercadoPagoGateway.create_pix_payment") as pix, patch("apps.payments.gateway.MercadoPagoGateway.create_card_payment") as card:
            for _ in range(2):
                response = self.client.post(self.interest_url(), {"consent": "on", "quantity": "10", "payment_method": "pix", "user": "forged"})
                self.assertRedirects(response, self.public_url())
            pix.assert_not_called()
            card.assert_not_called()
        interest = RaffleInterest.objects.get()
        self.assertEqual(interest.raffle, raffle)
        self.assertEqual(interest.user, self.user)
        self.assertEqual(interest.consent_text, RAFFLE_INTEREST_CONSENT)
        self.assertEqual(AuditLog.objects.filter(action="RAFFLE_INTEREST_REGISTERED").count(), 1)
        for model in (Order, ChargeAttempt, FinancialEntry, Registration):
            self.assertFalse(model.objects.exists())

    def test_interest_requires_public_campaign_opt_in_and_active_event(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.interest_url(), {"consent": "on"}).status_code, 404)
        raffle = self.public_raffle(is_public=False)
        self.assertEqual(self.client.post(self.interest_url(), {"consent": "on"}).status_code, 404)
        raffle.is_public = True
        raffle.interest_enabled = False
        raffle.save()
        self.assertEqual(self.client.post(self.interest_url(), {"consent": "on"}).status_code, 404)
        raffle.interest_enabled = True
        raffle.save()
        self.event.status = EventStatus.CANCELED
        self.event.save(update_fields=["status"])
        self.assertEqual(self.client.post(self.interest_url(), {"consent": "on"}).status_code, 403)
        self.assertFalse(RaffleInterest.objects.exists())

    def test_interest_audit_failure_rolls_back_consent_record(self):
        self.public_raffle()
        self.client.force_login(self.user)
        with patch("apps.events.views_raffles.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.interest_url(), {"consent": "on"})
        self.assertFalse(RaffleInterest.objects.exists())

    def test_management_interests_do_not_leak_between_events(self):
        own = self.public_raffle()
        other = self.public_raffle(event=self.other)
        visitor = User.objects.create_user(email="privado.outro@example.test", password="local-test-only")
        RaffleInterest.objects.create(raffle=other, user=visitor, consent_text=RAFFLE_INTEREST_CONSENT)
        manager = self.manager()
        RaffleInterest.objects.create(raffle=own, user=manager, consent_text=RAFFLE_INTEREST_CONSENT)
        response = self.client.get(self.management_url())
        self.assertContains(response, manager.email)
        self.assertNotContains(response, visitor.email)
        self.assertEqual(response.context["interest_count"], 1)
