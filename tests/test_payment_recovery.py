from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from apps.payments.gateway import GatewayError
from apps.payments.models import ChargeAttempt
from tests import test_payments as payment_fixtures
from tests.test_payments import MP_TEST_SETTINGS, response


@override_settings(**MP_TEST_SETTINGS)
class PaymentRecoveryTests(TestCase):
    setUp = payment_fixtures.PaymentReconciliationTests.setUp
    payment = payment_fixtures.PaymentReconciliationTests.payment

    def url(self):
        return reverse("checkout:refresh_payment", args=[self.order.pk])

    def test_recovers_missed_webhook_with_get_only_and_creates_no_second_charge(self):
        self.charge.provider_payment_ref = "998877"
        self.charge.save(update_fields=["provider_payment_ref"])
        self.http.side_effect = None
        self.http.return_value = response(self.payment())
        result = self.client.post(self.url())
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["is_paid"])
        self.assertEqual(ChargeAttempt.objects.count(), 1)
        self.assertTrue(all(call.args[0] == "GET" for call in self.http.call_args_list))
        self.assertIn("no-store", result["Cache-Control"])

    def test_repeated_queries_are_throttled_in_database(self):
        with patch("apps.payments.services.refresh_charge") as refresh:
            self.client.post(self.url())
            self.client.post(self.url())
            refresh.assert_called_once()
        self.charge.refresh_from_db()
        self.assertIsNotNone(self.charge.last_checked_at)

    def test_unavailable_provider_does_not_change_to_paid_or_create_charge(self):
        with patch("apps.payments.services.refresh_charge", side_effect=GatewayError("TIMEOUT", "offline")):
            result = self.client.post(self.url())
        self.assertFalse(result.json()["is_paid"])
        self.assertEqual(ChargeAttempt.objects.count(), 1)

    @override_settings(MP_ACCESS_TOKEN="")
    def test_unconfigured_account_does_not_call_provider(self):
        result = self.client.post(self.url())
        self.assertIn("indisponível", result.json()["message"])
        self.http.assert_not_called()

    def test_scope_and_methods_and_csrf(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)
        self.assertEqual(Client().post(self.url()).status_code, 404)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url()).status_code, 403)
