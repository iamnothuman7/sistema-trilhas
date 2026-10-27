"""The next action must reflect payment, personal acceptance and ownership."""
from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.checkout.models import OrderStatus
from apps.identity.models import UserPersonLink
from apps.registrations.models import RegistrationCredential
from tests import test_participant_access as fixtures


class ParticipantJourneyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.ParticipantAccessTests.setUpTestData.__func__(cls)
        cls.event.status = "PUBLISHED"
        cls.event.save()

    def dashboard(self):
        return self.client.get(reverse("minha_conta:dashboard"))

    def terms_url(self):
        return reverse("minha_conta:registration_detail", args=[self.reg.pk]) + "#termos"

    def link_holder(self):
        UserPersonLink.objects.create(user=self.holder, person=self.person)
        self.client.force_login(self.holder)

    def accept(self):
        return self.client.post(reverse("minha_conta:accept_terms", args=[self.reg.pk]), {
            "document_version_id": [str(self.regulation.pk), str(self.terms.pk)]})

    def test_public_nav_links_login_to_participant_area_and_keeps_safe_destination(self):
        url = reverse("minha_conta:dashboard")
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, 'class="nav-account"')
        self.assertContains(response, reverse("identity:login") + "?next=" + url)
        self.buyer.set_password("Test-account-only-2027")
        self.buyer.save()
        response = self.client.post(reverse("identity:login") + "?next=" + url,
            {"email": self.buyer.email, "password": "Test-account-only-2027"})
        self.assertRedirects(response, url)
        self.assertContains(self.dashboard(), "Meu perfil e minhas inscrições")

    def test_paid_holder_gets_terms_and_art_then_direct_qr_after_acceptance(self):
        self.link_holder()
        response = self.dashboard()
        self.assertContains(response, self.terms_url())
        self.assertContains(response, "Liberar meu QR Code")
        self.assertContains(response, "Criar story e feed")
        self.assertFalse(RegistrationCredential.objects.exists())
        self.assertRedirects(self.accept(), reverse("minha_conta:credential_view", args=[self.reg.pk]))
        response = self.dashboard()
        self.assertContains(response, "Ver meu QR Code")
        self.assertNotContains(response, "Liberar meu QR Code")

    def test_pending_payment_is_only_resumable_by_buyer_and_never_unlocks_art(self):
        self.order.status = OrderStatus.AWAITING_PAYMENT
        self.order.save()
        self.client.force_login(self.buyer)
        response = self.dashboard()
        self.assertContains(response, "Continuar pagamento")
        self.assertNotContains(response, "Criar story e feed")
        self.link_holder()
        response = self.dashboard()
        self.assertNotContains(response, "Continuar pagamento")
        self.assertContains(response, "O comprador precisa concluir")
        self.assertRedirects(self.accept(), reverse("minha_conta:registration_detail", args=[self.reg.pk]))
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_expired_payment_does_not_offer_retry(self):
        self.order.status = OrderStatus.AWAITING_PAYMENT
        self.order.expires_at = timezone.now() - timedelta(minutes=1)
        self.order.save()
        self.client.force_login(self.buyer)
        self.assertContains(self.dashboard(), "Reserva expirada")
        self.assertNotContains(self.dashboard(), "Continuar pagamento")

    def test_buyer_of_another_participant_gets_personal_claim_or_holder_guidance(self):
        self.client.force_login(self.buyer)
        self.assertContains(self.dashboard(), "Confirmar participante")
        UserPersonLink.objects.create(user=self.holder, person=self.person)
        self.assertContains(self.dashboard(), "Ver termos do participante")
        self.assertNotContains(self.dashboard(), "Liberar meu QR Code")
        self.assertEqual(self.accept().status_code, 403)

    def test_revoked_or_refunded_registration_never_shows_ready_action(self):
        self.link_holder()
        self.accept()
        credential = RegistrationCredential.objects.get(registration=self.reg)
        credential.revoked_at = timezone.now()
        credential.save()
        self.assertNotContains(self.dashboard(), "Ver meu QR Code")
        self.order.status = OrderStatus.REFUNDED
        self.order.save()
        response = self.dashboard()
        self.assertNotContains(response, "Criar story e feed")
        self.assertNotContains(response, "Ver meu QR Code")
        self.assertNotContains(response, "Comprovante PDF")

    def test_art_after_purchase_goes_directly_to_own_terms_then_qr(self):
        self.link_holder()
        url = reverse("events:frames", args=[self.event.slug])
        response = self.client.get(url, {"inscricao": self.reg.pk, "origem": "compra"})
        self.assertContains(response, self.terms_url())
        self.accept()
        response = self.client.get(url, {"inscricao": self.reg.pk, "origem": "compra"})
        self.assertContains(response, "Ver meu QR Code")

    def test_foreign_account_has_no_registration_or_actions(self):
        self.client.force_login(self.outsider)
        response = self.dashboard()
        self.assertNotContains(response, self.person.full_name)
        self.assertNotContains(response, "data-registration-next")
        self.assertContains(response, "Encontrar meu evento")

    def test_buyer_confirms_own_ticket_without_claim_link_but_still_must_accept_terms(self):
        self.client.force_login(self.buyer)
        url = reverse("minha_conta:claim_own_registration", args=[self.reg.pk])
        detail = reverse("minha_conta:registration_detail", args=[self.reg.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), detail + "#participante")
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())
        for _ in range(2):
            self.assertRedirects(self.client.post(url, {"confirm_holder": "yes"}), self.terms_url())
        self.assertEqual(UserPersonLink.objects.filter(user=self.buyer, person=self.person).count(), 1)
        self.assertFalse(self.reg.terms_acceptances.exists())
        self.assertFalse(RegistrationCredential.objects.exists())
        self.assertRedirects(self.accept(), reverse("minha_conta:credential_view", args=[self.reg.pk]))

    def test_direct_confirmation_never_takes_over_another_holder(self):
        self.link_holder()
        self.client.force_login(self.buyer)
        response = self.client.post(reverse("minha_conta:claim_own_registration", args=[self.reg.pk]), {"confirm_holder": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(UserPersonLink.objects.filter(person=self.person).values_list("user_id", flat=True)), [self.holder.pk])

    def test_direct_confirmation_requires_authenticated_buyer_and_csrf(self):
        from django.test import Client
        url = reverse("minha_conta:claim_own_registration", args=[self.reg.pk])
        session = self.client.session
        session["checkout_order_ids"] = [str(self.order.pk)]
        session.save()
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 404)
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(self.buyer)
        self.assertEqual(secured.post(url, {"confirm_holder": "yes"}).status_code, 403)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())

    def test_direct_confirmation_rejects_pending_refunded_or_canceled_registration(self):
        self.client.force_login(self.buyer)
        url = reverse("minha_conta:claim_own_registration", args=[self.reg.pk])
        for status in (OrderStatus.AWAITING_PAYMENT, OrderStatus.REFUNDED):
            self.order.status = status
            self.order.save()
            self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 403)
        self.order.status = OrderStatus.PAID
        self.order.save()
        self.reg.commercial_state = "CANCELED"
        self.reg.save()
        self.assertEqual(self.client.post(url, {"confirm_holder": "yes"}).status_code, 403)
        self.assertFalse(UserPersonLink.objects.filter(person=self.person).exists())
