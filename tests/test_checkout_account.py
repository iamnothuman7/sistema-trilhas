from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.checkout.models import OrderStatus
from apps.identity.models import User, CheckoutAccount, UserPersonLink
from apps.identity.onboarding import provision_buyer_account
from apps.payments.services import reconcile_payment
from tests import test_payments as payment_fixtures
from tests.test_payments import MP_TEST_SETTINGS


@override_settings(**MP_TEST_SETTINGS)
class PurchaseAccountTests(TestCase):
    setUp = payment_fixtures.PaymentReconciliationTests.setUp
    payment = payment_fixtures.PaymentReconciliationTests.payment

    def url(self):
        return reverse("identity:purchase_access", args=[self.order.pk])

    def paid(self):
        reconcile_payment(self.payment())
        self.order.refresh_from_db()
        return self.order.purchaser_user

    def test_confirmed_payment_creates_new_account_once_without_password_or_holder_claim(self):
        user = self.paid()
        self.assertIsNotNone(user)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, self.order.purchaser_email)
        self.assertEqual(CheckoutAccount.objects.get(order=self.order).user, user)
        reconcile_payment(self.payment())
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserPersonLink.objects.exists())

    def test_pending_payment_does_not_create_account(self):
        reconcile_payment(self.payment(status="pending"))
        self.assertFalse(User.objects.exists())
        self.assertIsNone(provision_buyer_account(self.order))
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_new_buyer_sets_password_in_original_session_then_sees_purchase(self):
        user = self.paid()
        password = "Trilhas-Strong-Access-2027"
        self.assertContains(self.client.get(self.url()), "Sua conta já está criada")
        response = self.client.post(self.url(), {"new_password1": password, "new_password2": password})
        self.assertRedirects(response, reverse("minha_conta:dashboard"), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertIsNotNone(CheckoutAccount.objects.get(user=user).activated_at)
        self.assertFalse(UserPersonLink.objects.exists())
        from apps.core.models import AuditLog
        audit = AuditLog.objects.get(action="CHECKOUT_ACCOUNT_ACTIVATED")
        self.assertEqual(str(audit.actor_id), str(user.pk))
        self.assertEqual(audit.actor_email, user.email)

    def test_other_session_or_same_email_cannot_set_first_password(self):
        self.paid()
        client = Client()
        self.assertEqual(client.get(self.url()).status_code, 404)
        self.assertEqual(client.post(self.url(), {"new_password1": "Trilhas-Strong-2027", "new_password2": "Trilhas-Strong-2027"}).status_code, 404)

    def test_existing_account_requires_password_and_never_is_overwritten(self):
        user = User.objects.create_user(email=self.order.purchaser_email, password="Existing-Account-2027", full_name="Existing")
        self.assertIsNone(self.paid())
        self.assertFalse(CheckoutAccount.objects.exists())
        bad = self.client.post(self.url(), {"password": "wrong"})
        self.assertEqual(bad.status_code, 400)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.purchaser_user_id)
        result = self.client.post(self.url(), {"password": "Existing-Account-2027"})
        self.assertEqual(result.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.purchaser_user, user)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Existing-Account-2027"))

    def test_first_access_replay_cannot_reset_password(self):
        self.paid()
        first = "Trilhas-Strong-Access-2027"
        self.client.post(self.url(), {"new_password1": first, "new_password2": first})
        self.client.logout()
        session = self.client.session
        session["checkout_order_ids"] = [str(self.order.pk)]
        session.save()
        result = self.client.post(self.url(), {"new_password1": "Changed-Password-2028", "new_password2": "Changed-Password-2028"})
        self.assertEqual(result.status_code, 400)
        self.order.purchaser_user.refresh_from_db()
        self.assertTrue(self.order.purchaser_user.check_password(first))

    def test_first_password_validation_and_private_headers(self):
        user = self.paid()
        result = self.client.post(self.url(), {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(result.status_code, 400)
        self.assertIn("no-store", result["Cache-Control"])
        self.assertEqual(result["Referrer-Policy"], "same-origin")
        self.assertNotContains(result, 'value="12345678"', status_code=400)
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())

    def test_refunded_purchase_cannot_activate_new_account(self):
        user = self.paid()
        self.order.status = OrderStatus.REFUNDED
        self.order.save(update_fields=["status"])
        self.assertEqual(self.client.post(self.url(), {"new_password1": "Strong-Pass-2027", "new_password2": "Strong-Pass-2027"}).status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())

    def test_csrf_required(self):
        self.paid()
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["checkout_order_ids"] = [str(self.order.pk)]
        session.save()
        self.assertEqual(client.post(self.url(), {}).status_code, 403)

    def test_same_origin_form_post_with_csrf_succeeds_but_null_origin_is_rejected(self):
        self.paid()
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["checkout_order_ids"] = [str(self.order.pk)]
        session.save()
        page = client.get(self.url(), secure=True)
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        token = client.cookies["csrftoken"].value
        data = {"csrfmiddlewaretoken": token, "new_password1": "Mobile-Test-Access-2027",
                "new_password2": "Mobile-Test-Access-2027"}
        rejected = client.post(self.url(), data, secure=True, HTTP_ORIGIN="null")
        self.assertEqual(rejected.status_code, 403)
        accepted = client.post(self.url(), data, secure=True, HTTP_ORIGIN="https://testserver")
        self.assertEqual(accepted.status_code, 302)
        self.assertIn("_auth_user_id", client.session)
