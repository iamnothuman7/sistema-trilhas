"""Account switches use fictitious credentials and mocked authenticated API replies."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.checkout.views import _payment_context
from apps.finance.models import FinancialEntry
from apps.identity.models import User
from apps.payments.connection import save_draft, verify_draft, activate_draft, ConnectionConflict
from apps.payments.gateway import MercadoPagoGateway, GatewayError
from apps.payments.gateway_config import (charge_gateway, decrypt_configuration, encrypt_configuration,
    environment_configuration, retain_configuration)
from apps.payments.models import (ChargeAttempt, MercadoPagoConnection, MercadoPagoAccountConfiguration,
    PaymentTransaction, ProviderEvent)
from apps.payments.services import reconcile_payment, refresh_charge, submit_charge, PaymentValidationError
from tests import test_payments as fixtures


@override_settings(**fixtures.MP_TEST_SETTINGS)
class AccountSnapshotTests(TestCase):
    setUp = fixtures.PaymentReconciliationTests.setUp
    payment = fixtures.PaymentReconciliationTests.payment

    def owner(self):
        return User.objects.get_or_create(email="account-owner@example.test", defaults={"is_superuser": True, "is_staff": True})[0]

    def switch(self, collector="5678", token="APP_USR-fictitious-B", secret="fictitious-B-signature", public="APP_USR-fictitious-public-B", **overrides):
        connection = MercadoPagoConnection.objects.filter(pk=1).first()
        values = dict(version=connection.revision if connection else 0, collector_id=collector,
            access_token=token, public_key=public, webhook_secret=secret, environment="sandbox")
        values.update(overrides)
        draft = save_draft(values, self.owner())
        with patch.object(MercadoPagoGateway, "get_account", return_value={"id": int(collector), "tags": ["test_user"]}):
            verified = verify_draft(draft.revision, self.owner())
        return activate_draft(dict(version=verified.revision, confirm_account=True, confirm_environment=True,
            confirm_webhook=True, confirm_sandbox=True), self.owner())

    def webhook(self, secret, data=None, request_id="account-switch-test"):
        data = data or self.payment()
        now = str(int(time.time()))
        digest = hmac.new(secret.encode(), f"id:{data['id']};request-id:{request_id};ts:{now};".encode(), hashlib.sha256).hexdigest()
        self.http.side_effect = None
        self.http.return_value = fixtures.response(data)
        return self.client.post(reverse("payments:webhook") + f"?data.id={data['id']}", json.dumps({"type": "payment", "data": {"id": str(data["id"])}}),
            content_type="application/json", HTTP_X_REQUEST_ID=request_id, HTTP_X_SIGNATURE=f"ts={now},v1={digest}")

    def test_switch_atomically_pins_legacy_attempt_to_environment_configuration(self):
        self.assertIsNone(self.charge.account_configuration_id)
        self.switch()
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.account_configuration.collector_id, "1234")
        gateway = charge_gateway(self.charge)
        self.assertEqual(gateway.access_token, fixtures.MP_TEST_SETTINGS["MP_ACCESS_TOKEN"])
        self.assertEqual(gateway.environment, "sandbox")
        self.assertEqual(MercadoPagoGateway().collector_id, "5678")
        self.assertEqual(MercadoPagoAccountConfiguration.objects.count(), 2)

    def test_old_signature_webhook_approves_old_account_after_switch(self):
        self.switch()
        result = self.webhook(fixtures.MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"])
        self.assertEqual(result.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PAID")
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer " + fixtures.MP_TEST_SETTINGS["MP_ACCESS_TOKEN"])
        self.assertEqual(FinancialEntry.objects.filter(kind="RECEITA_INSCRICAO").count(), 1)
        self.assertEqual(self.webhook(fixtures.MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"]).status_code, 200)
        self.assertEqual(FinancialEntry.objects.filter(kind="RECEITA_INSCRICAO").count(), 1)

    def test_webhook_new_account_cannot_settle_old_charge(self):
        self.switch()
        result = self.webhook("fictitious-B-signature", self.payment(collector_id=5678))
        self.assertEqual(result.status_code, 422)
        self.assertFalse(FinancialEntry.objects.exists())
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_historical_payment_refresh_uses_original_token_and_preserves_refund_history(self):
        self.switch()
        self.charge.refresh_from_db()
        self.charge.provider_payment_ref = "998877"
        self.charge.save()
        self.http.side_effect = None
        self.http.return_value = fixtures.response(self.payment())
        refresh_charge(self.charge)
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer " + fixtures.MP_TEST_SETTINGS["MP_ACCESS_TOKEN"])
        returned = self.payment(status="refunded", transaction_amount_refunded=180.01,
            date_last_updated=(self.now+timedelta(minutes=1)).isoformat())
        self.http.return_value = fixtures.response(returned)
        refresh_charge(self.charge)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "REFUNDED")
        self.assertEqual(self.order.refunds.get().amount_cents, 18001)
        self.assertEqual(FinancialEntry.objects.filter(kind="REEMBOLSO").get().amount_cents_signed, -18001)

    def test_first_new_charge_after_switch_is_pinned_before_provider_request(self):
        self.switch()
        self.charge.delete()
        self.http.reset_mock()
        def authenticated_reply(*args, **kwargs):
            charge = ChargeAttempt.objects.get(order=self.order)
            self.assertIsNotNone(charge.account_configuration_id)
            self.assertEqual(charge.account_configuration.collector_id, "5678")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer APP_USR-fictitious-B")
            self.assertEqual(kwargs["json"]["metadata"]["environment"], "sandbox")
            self.assertEqual(kwargs["json"]["metadata"]["collector_id"], "5678")
            return fixtures.response(self.payment(collector_id=5678, metadata=kwargs["json"]["metadata"]))
        self.http.side_effect = authenticated_reply
        context = _payment_context(self.order)
        result = self.client.post(reverse("checkout:process_payment", args=[self.order.pk]),
            {"payment_method": "pix", "attempt_key": context["attempt_key"], "payment_quote": context["pix_quote"].token})
        self.assertEqual(result.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PAID")
        self.assertEqual(self.http.call_count, 1)

    def test_inflight_submission_uses_pinned_configuration_after_account_switch(self):
        self.assertIsNone(self.charge.account_configuration_id)
        self.switch()
        # The in-flight Python object predates activation's bulk backfill.
        self.assertIsNone(self.charge.account_configuration_id)
        self.http.side_effect = None
        self.http.return_value = fixtures.response(self.payment())
        submit_charge(self.charge)
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer " + fixtures.MP_TEST_SETTINGS["MP_ACCESS_TOKEN"])

    def test_account_switch_between_configuration_and_fk_reads_uses_previous_account(self):
        from apps.payments import gateway_config
        original_loader = gateway_config.load_gateway_configuration
        def switch_after_loading():
            previous = original_loader()
            self.switch()
            return previous
        with patch("apps.payments.gateway_config.load_gateway_configuration", side_effect=switch_after_loading):
            gateway = charge_gateway(self.charge)
        self.assertEqual(gateway.collector_id, "1234")
        self.assertEqual(MercadoPagoGateway().collector_id, "5678")

    def test_activation_audit_failure_rolls_back_legacy_binding_and_historical_credentials(self):
        with patch("apps.payments.connection._audit", side_effect=lambda connection, user, action, **extra: (_ for _ in ()).throw(RuntimeError("audit unavailable")) if action == "MP_CONNECTION_ACTIVATED" else None):
            with self.assertRaises(RuntimeError):
                self.switch()
        self.charge.refresh_from_db()
        self.assertIsNone(self.charge.account_configuration_id)
        self.assertFalse(MercadoPagoAccountConfiguration.objects.exists())
        self.assertFalse(MercadoPagoConnection.objects.get().active_ciphertext)

    def test_stale_card_form_does_not_send_token_to_different_application(self):
        self.charge.delete()
        previous = _payment_context(self.order)
        self.switch()
        self.http.reset_mock()
        result = self.client.post(reverse("checkout:process_payment", args=[self.order.pk]), {"payment_method": "credit_card",
            "attempt_key": previous["attempt_key"], "card_token": "fictitious-card-token", "payment_method_id": "visa", "installments": "1"})
        self.assertEqual(result.status_code, 302)
        self.http.assert_not_called()
        self.assertFalse(ChargeAttempt.objects.exists())

    def test_rotating_token_updates_same_snapshot_including_pre_rotation_instances(self):
        self.switch()
        self.charge.refresh_from_db()
        old_account_id = self.charge.account_configuration_id
        self.switch(collector="1234", token="TEST-new-A-token", secret=fixtures.MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"], public=fixtures.MP_TEST_SETTINGS["MP_PUBLIC_KEY"])
        self.assertEqual(self.charge.account_configuration_id, old_account_id)
        self.assertEqual(charge_gateway(self.charge).access_token, "TEST-new-A-token")
        self.assertEqual(MercadoPagoAccountConfiguration.objects.count(), 2)

    def test_secret_rotation_accepts_both_old_and_new_signatures_for_previous_charge(self):
        self.switch(collector="1234", token=fixtures.MP_TEST_SETTINGS["MP_ACCESS_TOKEN"], secret="fictitious-rotated-secret", public=fixtures.MP_TEST_SETTINGS["MP_PUBLIC_KEY"])
        self.assertEqual(self.webhook("fictitious-rotated-secret").status_code, 200)
        self.assertEqual(self.webhook(fixtures.MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"], request_id="old-secret-retry").status_code, 200)
        self.assertEqual(FinancialEntry.objects.filter(kind="RECEITA_INSCRICAO").count(), 1)

    def test_provider_environment_and_metadata_must_match_historical_snapshot(self):
        self.switch()
        for changes in ({"live_mode": True}, {"collector_id": 5678}, {"metadata": {"order_id": str(self.order.pk), "charge_attempt_id": str(self.charge.pk), "environment": "production", "collector_id": "1234"}}):
            with self.subTest(changes=changes), self.assertRaises(PaymentValidationError):
                reconcile_payment(self.payment(**changes))
        self.assertFalse(FinancialEntry.objects.exists())

    def test_unknown_signature_creates_no_event_or_provider_request(self):
        self.switch()
        self.http.reset_mock()
        self.assertEqual(self.webhook("fictitious-unknown-signature").status_code, 401)
        self.http.assert_not_called()
        self.assertFalse(ProviderEvent.objects.exists())

    def test_unrecoverable_previous_credentials_block_switch_without_reassigning_legacy_attempt(self):
        with override_settings(MP_ACCESS_TOKEN=""):
            with self.assertRaises(ConnectionConflict):
                self.switch()
        self.charge.refresh_from_db()
        self.assertIsNone(self.charge.account_configuration_id)
        self.assertFalse(MercadoPagoConnection.objects.get().active_ciphertext)

    def test_snapshot_decryption_failure_fails_closed_instead_of_using_active_account(self):
        self.switch()
        self.charge.refresh_from_db()
        MercadoPagoAccountConfiguration.objects.filter(pk=self.charge.account_configuration_id).update(credentials_ciphertext="damaged")
        self.assertTrue(charge_gateway(self.charge).configuration_issues())
        with self.assertRaises(GatewayError):
            refresh_charge(self.charge)
        self.http.assert_not_called()

    def test_cannot_submit_or_reconcile_snapshot_from_other_deployment_environment(self):
        values = environment_configuration()
        values["environment"] = "production"
        self.charge.account_configuration = retain_configuration(values)
        self.charge.save()
        with self.assertRaises(GatewayError):
            submit_charge(self.charge)
        with self.assertRaises(GatewayError):
            reconcile_payment(self.payment(live_mode=True))
        self.http.assert_not_called()
        self.assertFalse(FinancialEntry.objects.exists())

    def test_replacement_mode_never_mixes_blank_fields_with_old_secrets(self):
        connection = save_draft(dict(version=0, operation_mode="replace", environment="sandbox", collector_id="5678",
            access_token="APP_USR-fictitious-B", public_key="", webhook_secret=""), self.owner())
        values = decrypt_configuration(connection.draft_ciphertext)
        self.assertEqual(values["public_key"], "")
        self.assertEqual(values["webhook_secret"], "")
        with self.assertRaises(GatewayError):
            verify_draft(connection.revision, self.owner())
        self.http.assert_not_called()

    def test_adding_another_account_is_restricted_and_secrets_remain_encrypted(self):
        self.switch()
        data = list(MercadoPagoAccountConfiguration.objects.values_list("credentials_ciphertext", flat=True))
        self.assertTrue(all("APP_USR-fictitious-B" not in item for item in data))
        self.client.force_login(self.owner())
        page = self.client.get(reverse("gestao:mercadopago_connection"))
        self.assertContains(page, "Adicionar outra conta")
        self.assertContains(page, "Conta 1234")
        self.assertNotContains(page, "APP_USR-fictitious-B")
        self.assertNotContains(page, "fictitious-B-signature")
