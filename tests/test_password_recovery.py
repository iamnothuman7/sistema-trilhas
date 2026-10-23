import re
import smtplib
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.checkout.models import Order, OrderStatus
from apps.core.models import AuditLog, Organization
from apps.events.models import Event
from apps.identity.models import CheckoutAccount, User, UserPersonLink


@override_settings(DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PUBLIC_SITE_URL="https://morcego.example.test", DEFAULT_FROM_EMAIL="acesso@example.test",
                   PASSWORD_RESET_TIMEOUT=3600, ALLOWED_HOSTS=["testserver", "untrusted.example.test"])
class PasswordRecoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        org = Organization.objects.create(name="Organização de teste")
        event = Event.objects.create(organization=org, title="Evento de teste", slug="recovery-test",
            starts_at=now+timedelta(days=10), ends_at=now+timedelta(days=11),
            registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(days=9))
        cls.user = User.objects.create_user(email="recovery@example.test", full_name="Conta normal", password="Before!934as-")
        cls.pending = User.objects.create_user(email="purchase@example.test", full_name="Conta da compra")
        cls.disabled = User.objects.create_user(email="disabled@example.test", full_name="Desativada", password="Before!934as-", is_active=False)
        cls.unusable = User.objects.create_user(email="unusable@example.test", full_name="Sem senha")
        cls.order = Order.objects.create(event=event, merchant_reference="RESET-TEST", purchaser_user=cls.pending,
            purchaser_name=cls.pending.full_name, purchaser_email=cls.pending.email, purchaser_phone="00000000",
            status=OrderStatus.PAID, expires_at=now+timedelta(hours=1), gross_cents=12000, total_cents=12000)
        cls.origin = CheckoutAccount.objects.create(user=cls.pending, order=cls.order)

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def request_url(self):
        return reverse("identity:password_reset")

    def request_link(self, email, client=None):
        response = (client or self.client).post(self.request_url(), {"email": email})
        self.assertRedirects(response, reverse("identity:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r"https://[^\s]+", mail.outbox[0].body).group()
        return urlsplit(url).path

    def token_url(self, user):
        return reverse("identity:password_reset_confirm", kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)), "token": default_token_generator.make_token(user),
        })

    def set_password(self, url, client=None, password="Updated!734-senHa"):
        client = client or self.client
        initial = client.get(url)
        self.assertEqual(initial.status_code, 302)
        clean_url = initial.url
        self.assertIn("set-password", clean_url)
        form_page = client.get(clean_url)
        self.assertContains(form_page, "Defina sua senha")
        self.assertEqual(form_page["Cache-Control"], "private, no-store")
        self.assertEqual(initial["Referrer-Policy"], "no-referrer")
        return client.post(clean_url, {"new_password1": password, "new_password2": password})

    def test_normal_user_recovers_once_without_auto_login_or_person_links(self):
        original_links = UserPersonLink.objects.count()
        url = self.request_link(self.user.email.upper())
        response = self.set_password(url)
        self.assertRedirects(response, reverse("identity:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Updated!734-senHa"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(UserPersonLink.objects.count(), original_links)
        self.assertContains(self.client.get(url), "Link indisponível")
        audit = AuditLog.objects.get(action="PASSWORD_RESET")
        self.assertEqual(audit.actor_id, str(self.user.pk))
        self.assertEqual(audit.diff_json, {})

    def test_pending_paid_checkout_can_activate_by_email_without_claiming_people(self):
        url = self.request_link(self.pending.email)
        self.assertRedirects(self.set_password(url), reverse("identity:password_reset_complete"))
        self.origin.refresh_from_db(); self.pending.refresh_from_db(); self.order.refresh_from_db()
        self.assertIsNotNone(self.origin.activated_at)
        self.assertTrue(self.pending.has_usable_password())
        self.assertEqual(self.order.purchaser_user_id, self.pending.pk)
        self.assertFalse(UserPersonLink.objects.exists())

    def test_unknown_disabled_and_arbitrary_unusable_accounts_receive_no_email(self):
        for email in ("missing@example.test", self.disabled.email, self.unusable.email):
            response = self.client.post(self.request_url(), {"email": email})
            self.assertRedirects(response, reverse("identity:password_reset_done"))
            self.assertFalse(mail.outbox)

    def test_pending_refunded_or_staff_accounts_cannot_be_activated(self):
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.REFUNDED)
        self.client.post(self.request_url(), {"email": self.pending.email})
        self.assertFalse(mail.outbox)
        self.assertContains(self.client.get(self.token_url(self.pending)), "Link indisponível")
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.PAID)
        User.objects.filter(pk=self.pending.pk).update(is_staff=True)
        cache.clear(); self.client.post(self.request_url(), {"email": self.pending.email})
        self.assertFalse(mail.outbox)

    def test_token_obtained_before_refund_does_not_activate_pending_account(self):
        url = self.request_link(self.pending.email)
        clean = self.client.get(url).url
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.PAID_NEEDS_REVIEW)
        response = self.client.post(clean, {"new_password1":"Updated!734-senHa", "new_password2":"Updated!734-senHa"})
        self.assertContains(response, "Link indisponível")
        self.pending.refresh_from_db(); self.origin.refresh_from_db()
        self.assertFalse(self.pending.has_usable_password())
        self.assertIsNone(self.origin.activated_at)

    def test_usable_account_can_recover_after_refund_to_view_history(self):
        self.pending.set_password("Before!934as-"); self.pending.save(update_fields=["password"])
        Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.REFUNDED)
        self.assertTrue(self.request_link(self.pending.email))

    def test_missing_smtp_or_unsafe_origin_never_generates_or_prints_reset_token(self):
        configurations = [
            {"EMAIL_BACKEND":"django.core.mail.backends.smtp.EmailBackend", "EMAIL_HOST":""},
            {"EMAIL_BACKEND":"django.core.mail.backends.console.EmailBackend"},
            {"EMAIL_BACKEND":"django.core.mail.backends.smtp.EmailBackend", "EMAIL_HOST":"smtp.example.test", "EMAIL_USE_TLS":True, "EMAIL_USE_SSL":True},
            {"PUBLIC_SITE_URL":"https://user:pass@untrusted.example.test/"},
            {"PUBLIC_SITE_URL":"https://untrusted.example.test/reset?next=bad"},
        ]
        for config in configurations:
            with self.settings(**config), patch("apps.identity.password_recovery.RecoveryForm.save") as save:
                response = self.client.post(self.request_url(), {"email": self.user.email})
                self.assertContains(response, "Nenhum link foi enviado")
                self.assertContains(response, reverse("core:contact"))
                save.assert_not_called()
        self.assertFalse(mail.outbox)

    def test_unavailable_recovery_offers_only_original_paid_purchase_session(self):
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST=""):
            access = reverse("identity:purchase_access", kwargs={"order_id":self.order.pk})
            self.assertNotContains(self.client.get(self.request_url()), access)
            session=self.client.session; session["checkout_order_ids"]=[str(self.order.pk)]; session.save()
            self.assertContains(self.client.get(self.request_url()), access)
            Order.objects.filter(pk=self.order.pk).update(status=OrderStatus.REFUNDED)
            self.assertNotContains(self.client.get(self.request_url()), access)

    def test_origin_is_fixed_and_requests_are_throttled_without_account_disclosure(self):
        response = self.client.post(self.request_url(), {"email":self.user.email}, HTTP_HOST="untrusted.example.test")
        self.assertEqual(response.status_code, 302)
        self.assertIn("https://morcego.example.test/", mail.outbox[0].body)
        self.assertNotIn("untrusted.example.test", mail.outbox[0].body)
        self.client.post(self.request_url(), {"email":self.user.email.upper()})
        self.assertEqual(len(mail.outbox), 1)

    def test_weak_password_is_rejected_and_csrf_required(self):
        url = self.request_link(self.user.email)
        response = self.set_password(url, password="123")
        self.assertContains(response, "8 caracteres")
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password("Before!934as-"))
        secure = Client(enforce_csrf_checks=True)
        self.assertEqual(secure.post(self.request_url(), {"email":self.user.email}).status_code, 403)
        self.assertEqual(secure.post(url, {"new_password1":"Updated!734-senHa", "new_password2":"Updated!734-senHa"}).status_code, 403)

    def test_two_open_tabs_cannot_reuse_token_to_overwrite_new_password(self):
        url = self.request_link(self.user.email)
        first, second = Client(), Client()
        clean1, clean2 = first.get(url).url, second.get(url).url
        first.post(clean1, {"new_password1":"First!734-senHa", "new_password2":"First!734-senHa"})
        second.post(clean2, {"new_password1":"Second!734-senHa", "new_password2":"Second!734-senHa"})
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password("First!734-senHa"))
        self.assertEqual(AuditLog.objects.filter(action="PASSWORD_RESET").count(), 1)

    def test_expired_tokens_do_not_change_password(self):
        with patch.object(default_token_generator, "_now", return_value=timezone.now().replace(tzinfo=None)-timedelta(days=2)):
            url = self.token_url(self.user)
        self.assertContains(self.client.get(url), "Link indisponível")
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password("Before!934as-"))

    def test_delivery_failure_is_generic_and_does_not_log_tokens(self):
        with patch("apps.identity.password_recovery.EmailMultiAlternatives.send", side_effect=smtplib.SMTPException("private smtp detail")), self.assertLogs("apps.identity.password_recovery", level="WARNING") as logs:
            response = self.client.post(self.request_url(), {"email":self.user.email}, follow=True)
        self.assertContains(response, "Solicitação recebida")
        self.assertContains(response, "serviço de e-mail estiver disponível")
        self.assertNotContains(response, self.user.email)
        self.assertNotIn("private smtp detail", " ".join(logs.output))
