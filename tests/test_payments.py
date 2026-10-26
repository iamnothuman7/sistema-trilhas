import hashlib
import hmac
import json
import time
import uuid
from copy import deepcopy
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.core import signing
from django.core.management import call_command, CommandError
from django.test import TestCase, SimpleTestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product, Lot, CapacityResource, CapacityHold, HoldStatus
from apps.checkout.models import Order, OrderLine, OrderStatus, ReceivableDue
from apps.core.models import Organization
from apps.events.models import Event, EventTheme, LegalDocumentVersion
from apps.finance.models import FinancialEntry, FinancialEntryKind
from apps.identity.models import Person, User, UserPersonLink
from apps.payments.gateway import MercadoPagoGateway, GatewayError
from apps.payments.models import ChargeAttempt, ChargeState, PaymentTransaction, ProviderEvent, PaymentAllocation
from apps.payments.services import reconcile_payment, PaymentValidationError, expire_order, refresh_charge, expire_stale_orders, activate_registration_if_eligible
from apps.registrations.models import Registration, Entitlement, RegistrationCredential, TermsAcceptance


MP_TEST_SETTINGS = dict(
    MP_ACCESS_TOKEN="TEST-unit-token", MP_PUBLIC_KEY="TEST-unit-public", MP_WEBHOOK_SECRET="unit-signing-secret",
    MP_COLLECTOR_ID="1234", MP_WEBHOOK_URL="https://trilhas.example/integracoes/mercadopago/webhook/",
    MP_ENVIRONMENT="sandbox", MP_API_BASE="https://api.mercadopago.com", MP_WEBHOOK_MAX_AGE_SECONDS=900,
)


def response(data, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


@override_settings(**MP_TEST_SETTINGS)
class GatewayTests(SimpleTestCase):
    def setUp(self):
        from apps.payments.gateway_config import GatewayConfiguration, environment_configuration
        patch("apps.payments.gateway_config.load_gateway_configuration", side_effect=lambda: GatewayConfiguration(environment_configuration())).start()
        self.now = timezone.now()
        self.charge = SimpleNamespace(id=uuid.uuid4(), idempotency_key=str(uuid.uuid4()), amount_cents=18001,
            pix_expiration=self.now + timedelta(minutes=30), order=SimpleNamespace(id=uuid.uuid4(),
                merchant_reference="UNIT-1", purchaser_name="Comprador Teste", purchaser_email="buyer@example.com",
                purchaser_document="", event=SimpleNamespace(title="Trilha Teste")))
        self.http = patch("apps.payments.gateway.requests.request").start()
        self.addCleanup(patch.stopall)
        self.http.side_effect = AssertionError("Unexpected network request")

    @override_settings(MP_ACCESS_TOKEN="", MP_WEBHOOK_SECRET="", MP_COLLECTOR_ID="")
    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(GatewayError) as context:
            MercadoPagoGateway().create_pix_payment(self.charge)
        self.assertEqual(context.exception.code, "NOT_CONFIGURED")
        self.http.assert_not_called()

    def test_pix_uses_payments_api_and_uuid_idempotency(self):
        self.http.side_effect = None
        self.http.return_value = response({"id": 123})
        MercadoPagoGateway().create_pix_payment(self.charge)
        args, kwargs = self.http.call_args
        self.assertEqual(args, ("POST", "https://api.mercadopago.com/v1/payments"))
        self.assertEqual(kwargs["json"]["payment_method_id"], "pix")
        self.assertEqual(kwargs["json"]["transaction_amount"], 180.01)
        self.assertEqual(kwargs["json"]["metadata"]["charge_attempt_id"], str(self.charge.id))
        self.assertEqual(kwargs["headers"]["X-Idempotency-Key"], self.charge.idempotency_key)
        self.assertEqual(kwargs["json"]["date_of_expiration"], self.charge.pix_expiration.isoformat(timespec="milliseconds"))
        self.assertNotIn("total_amount", kwargs["json"])

    def test_card_sends_token_not_raw_card_data(self):
        self.http.side_effect = None
        self.http.return_value = response({"id": 123})
        MercadoPagoGateway().create_card_payment(self.charge, token="one-use-token", payment_method_id="visa", installments=2)
        payload = self.http.call_args.kwargs["json"]
        self.assertEqual(payload["token"], "one-use-token")
        self.assertEqual(payload["installments"], 2)
        self.assertNotIn("card_number", payload)
        self.assertNotIn("security_code", payload)

    def test_timeout_is_uncertain_never_a_rejection_or_success(self):
        self.http.side_effect = requests.Timeout()
        with self.assertRaises(GatewayError) as context:
            MercadoPagoGateway().create_pix_payment(self.charge)
        self.assertTrue(context.exception.uncertain)

    def test_provider_errors_do_not_expose_response(self):
        self.http.side_effect = None
        self.http.return_value = response({"message": "sensitive-response"}, status=400)
        with self.assertRaises(GatewayError) as context:
            MercadoPagoGateway().create_pix_payment(self.charge)
        self.assertNotIn("sensitive-response", str(context.exception))
        self.assertFalse(context.exception.uncertain)

    def test_missing_webhook_secret_is_not_a_bypass(self):
        with override_settings(MP_WEBHOOK_SECRET=""):
            self.assertFalse(MercadoPagoGateway().verify_webhook_signature("", "request", "123"))

    def test_check_command_does_not_connect_by_default_or_print_secrets(self):
        output = StringIO()
        call_command("check_mercadopago", stdout=output)
        self.http.assert_not_called()
        self.assertNotIn(MP_TEST_SETTINGS["MP_ACCESS_TOKEN"], output.getvalue())
        self.assertNotIn(MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"], output.getvalue())

    def test_check_account_detects_wrong_receiver(self):
        self.http.side_effect = None
        self.http.return_value = response({"id": 9876})
        with self.assertRaises(CommandError):
            call_command("check_mercadopago", verify_account=True, stdout=StringIO())
        self.assertEqual(self.http.call_args.args[0], "GET")


@override_settings(**MP_TEST_SETTINGS)
class PaymentReconciliationTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        org = Organization.objects.create(name="Equipe de Testes")
        self.event = Event.objects.create(organization=org, title="Evento de Testes", slug="payment-tests",
            starts_at=self.now+timedelta(days=10), ends_at=self.now+timedelta(days=11),
            registration_opens_at=self.now-timedelta(days=1), registration_closes_at=self.now+timedelta(days=9))
        EventTheme.objects.create(event=self.event)
        self.product = Product.objects.create(event=self.event, name="Piloto", includes_kit=True, includes_shirt=True, includes_meal=False)
        self.order = Order.objects.create(event=self.event, merchant_reference="UNIT-PAY-1", purchaser_name="Comprador Teste",
            purchaser_email="buyer@example.com", purchaser_phone="00000000000", total_cents=18001, gross_cents=18001,
            status=OrderStatus.AWAITING_PAYMENT, expires_at=self.now+timedelta(minutes=35))
        self.line = OrderLine.objects.create(order=self.order, product=self.product, intended_person_name="Participante Teste", unit_cents=18001, net_cents=18001)
        self.person = Person.objects.create(full_name="Participante Teste")
        self.registration = Registration.objects.create(event=self.event, person=self.person, source_order_line=self.line)
        self.capacity = CapacityResource.objects.create(event=self.event, type="EVENT_OVERALL", capacity_total=10, reserved_count=1)
        self.hold = CapacityHold.objects.create(resource=self.capacity, order_id=self.order.id, order_line_id=self.line.id,
            quantity=1, expires_at=self.order.expires_at, idempotency_key=str(uuid.uuid4()))
        ReceivableDue.objects.create(order=self.order, due_type="FULL", due_at=self.order.expires_at, amount_cents=18001)
        self.charge = ChargeAttempt.objects.create(order=self.order, idempotency_key=str(uuid.uuid4()), amount_cents=18001,
            payment_method="pix", state=ChargeState.PENDING, pix_expiration=self.now+timedelta(minutes=30))
        session = self.client.session
        session["checkout_order_ids"] = [str(self.order.id)]
        session.save()
        self.http = patch("apps.payments.gateway.requests.request").start()
        self.addCleanup(patch.stopall)
        self.http.side_effect = AssertionError("Unexpected network request")

    def payment(self, status="approved", **overrides):
        data = {"id": 998877, "status": status, "transaction_amount": 180.01, "transaction_amount_refunded": 0,
            "currency_id": "BRL", "collector_id": 1234, "live_mode": False,
            "external_reference": self.order.merchant_reference,
            "metadata": {"order_id": str(self.order.id), "charge_attempt_id": str(self.charge.id)},
            "date_last_updated": self.now.isoformat(), "date_approved": self.now.isoformat(),
            "payment_method_id": "pix", "payment_type_id": "bank_transfer",
            "fee_details": [{"amount": 1.80, "fee_payer": "collector", "type": "mercadopago_fee"}],
            "point_of_interaction": {"transaction_data": {"qr_code": "REAL_PROVIDER_FIXTURE", "qr_code_base64": "aW1hZ2U="}}}
        data.update(overrides)
        return data

    def webhook(self, payment, *, request_id="notification-1", overrides=None, signature=None):
        self.http.side_effect = None
        self.http.return_value = response(payment)
        timestamp = str(int(time.time()))
        manifest = f"id:998877;request-id:{request_id};ts:{timestamp};"
        digest = hmac.new(MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"].encode(), manifest.encode(), hashlib.sha256).hexdigest()
        body = {"id": request_id, "type": "payment", "action": "payment.updated", "data": {"id": "998877"}}
        body.update(overrides or {})
        return self.client.post(reverse("payments:webhook")+"?data.id=998877&type=payment", data=json.dumps(body),
            content_type="application/json", HTTP_X_REQUEST_ID=request_id,
            HTTP_X_SIGNATURE=signature or f"ts={timestamp}, v1={digest}")

    def assert_unpaid(self):
        self.order.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertNotEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(self.registration.commercial_state, "RESERVED")
        self.assertFalse(FinancialEntry.objects.exists())

    def test_pending_does_not_grant_registration(self):
        reconcile_payment(self.payment("pending"))
        self.assert_unpaid()
        self.assertFalse(Entitlement.objects.exists())

    def test_approval_is_atomic_and_idempotent_with_real_fees(self):
        reconcile_payment(self.payment())
        reconcile_payment(self.payment())
        self.order.refresh_from_db(); self.capacity.refresh_from_db(); self.registration.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual((self.capacity.reserved_count, self.capacity.committed_count), (0, 1))
        self.assertEqual(self.registration.commercial_state, "ACTIVE")
        self.assertEqual(self.registration.operational_state, "NOT_READY")
        self.assertFalse(RegistrationCredential.objects.exists())
        self.assertEqual(Entitlement.objects.count(), 3)
        self.assertEqual(PaymentAllocation.objects.count(), 1)
        self.assertEqual(FinancialEntry.objects.count(), 2)
        self.assertEqual(FinancialEntry.objects.get(kind=FinancialEntryKind.TAXA_MERCADO_PAGO).amount_cents_signed, -180)
        self.assertEqual(self.order.receivables.get().allocated_paid_cents, 18001)

    def test_no_invented_fee_when_provider_omits_it(self):
        reconcile_payment(self.payment(fee_details=None))
        self.assertEqual(FinancialEntry.objects.count(), 1)

    def test_current_terms_required_for_credential(self):
        document = LegalDocumentVersion.objects.create(event=self.event, kind="REGULAMENTO", version_number=1, content="Regras")
        user = User.objects.create_user(email="unit@example.com", password="unit-password", full_name="Teste")
        UserPersonLink.objects.create(user=user, person=self.person)
        TermsAcceptance.objects.create(registration=self.registration, document_version=document, accepting_user=user, evidence_hash="a"*64)
        reconcile_payment(self.payment())
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.operational_state, "READY")
        self.assertTrue(RegistrationCredential.objects.get().is_valid)

    def test_acceptance_by_unlinked_user_cannot_release_credential(self):
        document = LegalDocumentVersion.objects.create(event=self.event, kind="REGULAMENTO", version_number=1, content="Regras")
        user = User.objects.create_user(email="unlinked@example.com", password="unit-password", full_name="Outro")
        TermsAcceptance.objects.create(registration=self.registration, document_version=document, accepting_user=user, evidence_hash="b"*64)
        reconcile_payment(self.payment())
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.operational_state, "NOT_READY")
        self.assertFalse(RegistrationCredential.objects.exists())

    def test_repeated_acceptance_cannot_revive_revoked_or_expired_credential(self):
        document = LegalDocumentVersion.objects.create(event=self.event, kind="REGULAMENTO", version_number=1, content="Regras")
        user = User.objects.create_user(email="holder@example.com", password="unit-password", full_name="Titular")
        UserPersonLink.objects.create(user=user, person=self.person)
        TermsAcceptance.objects.create(registration=self.registration, document_version=document, accepting_user=user, evidence_hash="c"*64)
        reconcile_payment(self.payment())
        credential = RegistrationCredential.objects.get(registration=self.registration)
        original_token = credential.raw_token
        credential.revoked_at = timezone.now(); credential.save()
        self.assertFalse(activate_registration_if_eligible(self.registration))
        credential.refresh_from_db(); self.registration.refresh_from_db()
        self.assertIsNotNone(credential.revoked_at)
        self.assertEqual(credential.raw_token, original_token)
        self.assertEqual(self.registration.operational_state, "NOT_READY")
        credential.revoked_at = None; credential.expires_at = timezone.now()-timedelta(seconds=1); credential.save()
        self.assertFalse(activate_registration_if_eligible(self.registration))
        credential.refresh_from_db()
        self.assertFalse(credential.is_valid)
        self.assertEqual(credential.raw_token, original_token)

    def test_mismatched_payment_fields_are_rejected(self):
        cases = [{"transaction_amount": 0.01}, {"transaction_amount": "NaN"}, {"transaction_amount": "180.011"},
            {"currency_id": "USD"}, {"collector_id": 111}, {"live_mode": True}, {"external_reference": "other-order"},
            {"metadata": {}}, {"payment_method_id": "visa"}, {"status": "unknown"}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(PaymentValidationError):
                reconcile_payment(self.payment(**change), charge_id=self.charge.id)
            self.assert_unpaid()

    def test_invalid_signature_rejected_before_api_lookup(self):
        result = self.webhook(self.payment(), signature="ts=123,v1=invalid")
        self.assertEqual(result.status_code, 401)
        self.http.assert_not_called()
        self.assertFalse(ProviderEvent.objects.exists())
        self.assert_unpaid()

    def test_unsigned_body_id_is_never_used_for_signature(self):
        result = self.client.post(reverse("payments:webhook"), json.dumps({"data": {"id": "998877"}}), content_type="application/json")
        self.assertEqual(result.status_code, 401)
        self.http.assert_not_called()

    def test_payload_cannot_claim_payment_approval(self):
        result = self.webhook(self.payment("pending"), overrides={"status": "approved", "external_reference": self.order.merchant_reference})
        self.assertEqual(result.status_code, 200)
        self.assert_unpaid()

    def test_webhook_pending_then_approved_same_payment_is_processed(self):
        self.assertEqual(self.webhook(self.payment("pending"), request_id="first").status_code, 200)
        approved = self.payment(date_last_updated=(self.now+timedelta(seconds=1)).isoformat())
        self.assertEqual(self.webhook(approved, request_id="second").status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(ProviderEvent.objects.count(), 2)

    def test_duplicate_notifications_do_not_duplicate_effects(self):
        first_response = self.webhook(self.payment())
        original_request = first_response.wsgi_request
        count = self.http.call_count
        # Replay the exact signed delivery, not a newly timestamped notification.
        replay = self.client.post(original_request.get_full_path(), data=original_request.body,
            content_type="application/json",
            HTTP_X_REQUEST_ID=original_request.headers["x-request-id"],
            HTTP_X_SIGNATURE=original_request.headers["x-signature"])
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(self.http.call_count, count)
        self.assertEqual(FinancialEntry.objects.count(), 2)
        self.assertEqual(ProviderEvent.objects.count(), 1)

    def test_webhook_uses_requested_resource_only(self):
        result = self.webhook(self.payment(id=111))
        self.assertEqual(result.status_code, 422)
        self.assert_unpaid()

    def test_old_pending_notification_cannot_regress_paid(self):
        reconcile_payment(self.payment())
        reconcile_payment(self.payment("pending", date_last_updated=(self.now-timedelta(seconds=5)).isoformat()))
        self.order.refresh_from_db(); self.charge.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID)
        self.assertEqual(self.charge.state, ChargeState.SUCCEEDED)

    def test_full_refund_reverses_ledger_and_revokes_entitlements_once(self):
        reconcile_payment(self.payment())
        refunded = self.payment("refunded", transaction_amount_refunded=180.01, fee_details=[], date_last_updated=(self.now+timedelta(minutes=1)).isoformat())
        reconcile_payment(refunded); reconcile_payment(refunded)
        self.order.refresh_from_db(); self.capacity.refresh_from_db(); self.registration.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.registration.commercial_state, "CANCELED")
        self.assertEqual(self.capacity.committed_count, 0)
        self.assertFalse(Entitlement.objects.exclude(state="REVOKED").exists())
        self.assertEqual(FinancialEntry.objects.filter(kind=FinancialEntryKind.REEMBOLSO).count(), 1)
        self.assertEqual(sum(FinancialEntry.objects.values_list("amount_cents_signed", flat=True)), 0)

    def test_partial_refund_suspends_access_for_manual_assignment(self):
        reconcile_payment(self.payment())
        reconcile_payment(self.payment(transaction_amount_refunded=50, date_last_updated=(self.now+timedelta(minutes=1)).isoformat()))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PARTIALLY_REFUNDED)
        self.assertFalse(Entitlement.objects.exclude(state="REVOKED").exists())
        self.assertEqual(FinancialEntry.objects.get(kind=FinancialEntryKind.REEMBOLSO).amount_cents_signed, -5000)

    def test_chargeback_is_never_success(self):
        reconcile_payment(self.payment())
        reconcile_payment(self.payment("charged_back", date_last_updated=(self.now+timedelta(minutes=1)).isoformat()))
        self.order.refresh_from_db(); self.charge.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.REFUNDED)
        self.assertEqual(self.charge.state, ChargeState.CANCELED)

    def test_rejected_payment_does_not_settle(self):
        reconcile_payment(self.payment("rejected"))
        self.assert_unpaid()
        self.charge.refresh_from_db()
        self.assertEqual(self.charge.state, ChargeState.FAILED)

    def test_expired_or_inconsistent_capacity_requires_review(self):
        self.hold.expires_at = self.now-timedelta(seconds=1); self.hold.save()
        reconcile_payment(self.payment())
        self.order.refresh_from_db(); self.capacity.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID_NEEDS_REVIEW)
        self.assertEqual(self.capacity.committed_count, 0)
        self.assertFalse(Entitlement.objects.exists())
        self.assertEqual(FinancialEntry.objects.filter(kind=FinancialEntryKind.RECEITA_INSCRICAO).count(), 1)

    def test_expiry_releases_reserved_capacity_once(self):
        self.order.expires_at = self.now-timedelta(seconds=1); self.order.save()
        expire_order(self.order.pk); expire_order(self.order.pk)
        self.capacity.refresh_from_db(); self.hold.refresh_from_db()
        self.assertEqual(self.capacity.reserved_count, 0)
        self.assertEqual(self.hold.status, HoldStatus.EXPIRED)

    def test_cleanup_includes_abandoned_orders_without_charge(self):
        self.charge.delete()
        self.order.expires_at = self.now-timedelta(minutes=1); self.order.save()
        self.assertEqual(expire_stale_orders(event_id=self.event.id), 1)
        self.capacity.refresh_from_db()
        self.assertEqual(self.capacity.reserved_count, 0)
        self.assertEqual(expire_stale_orders(event_id=self.event.id), 0)

    def test_second_payment_does_not_double_fulfillment(self):
        reconcile_payment(self.payment())
        second = ChargeAttempt.objects.create(order=self.order, amount_cents=18001, idempotency_key=str(uuid.uuid4()), payment_method="pix")
        data = self.payment(id=112233, metadata={"order_id": str(self.order.id), "charge_attempt_id": str(second.id)})
        reconcile_payment(data)
        self.capacity.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.capacity.committed_count, 1)
        self.assertEqual(Entitlement.objects.count(), 3)
        self.assertEqual(self.order.status, OrderStatus.PAID_NEEDS_REVIEW)

    def test_refund_of_duplicate_payment_preserves_original_entitlements(self):
        reconcile_payment(self.payment())
        second = ChargeAttempt.objects.create(order=self.order, amount_cents=18001, idempotency_key=str(uuid.uuid4()), payment_method="pix")
        data = self.payment(id=112233, metadata={"order_id": str(self.order.id), "charge_attempt_id": str(second.id)})
        reconcile_payment(data)
        data.update(status="refunded", transaction_amount_refunded=180.01, date_last_updated=(self.now+timedelta(minutes=1)).isoformat())
        reconcile_payment(data)
        self.capacity.refresh_from_db(); self.registration.refresh_from_db()
        self.assertEqual(self.capacity.committed_count, 1)
        self.assertEqual(self.registration.commercial_state, "ACTIVE")
        self.assertTrue(Entitlement.objects.filter(state="AVAILABLE").exists())

    def test_canceled_event_requires_review_instead_of_delivery(self):
        self.event.status = "CANCELED"; self.event.save()
        reconcile_payment(self.payment())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PAID_NEEDS_REVIEW)
        self.assertFalse(Entitlement.objects.exists())

    def test_lot_inventory_is_committed_and_released_once(self):
        lot = Lot.objects.create(event=self.event, product=self.product, title="Lote", price_cents=18001,
            opens_at=self.now-timedelta(days=1), closes_at=self.now+timedelta(days=1), max_quantity=1)
        self.line.lot = lot; self.line.save()
        reconcile_payment(self.payment()); reconcile_payment(self.payment())
        lot.refresh_from_db(); self.assertEqual(lot.current_sold, 1)
        data = self.payment("refunded", date_last_updated=(self.now+timedelta(minutes=1)).isoformat())
        reconcile_payment(data); reconcile_payment(data)
        lot.refresh_from_db(); self.assertEqual(lot.current_sold, 0)

    def test_provider_retries_after_failure_are_not_lost(self):
        self.http.side_effect = None
        self.http.return_value = response({}, 500)
        timestamp = str(int(time.time()))
        digest = hmac.new(MP_TEST_SETTINGS["MP_WEBHOOK_SECRET"].encode(), f"id:998877;request-id:retry;ts:{timestamp};".encode(), hashlib.sha256).hexdigest()
        url = reverse("payments:webhook")+"?data.id=998877"
        headers = {"HTTP_X_REQUEST_ID": "retry", "HTTP_X_SIGNATURE": f"ts={timestamp},v1={digest}"}
        result = self.client.post(url, json.dumps({"type":"payment", "data":{"id":"998877"}}), content_type="application/json", **headers)
        self.assertEqual(result.status_code, 503)
        self.assertIsNone(ProviderEvent.objects.get().processed_at)
        self.http.return_value = response(self.payment())
        result = self.client.post(url, json.dumps({"type":"payment", "data":{"id":"998877"}}), content_type="application/json", **headers)
        self.assertEqual(result.status_code, 200)
        self.order.refresh_from_db(); self.assertEqual(self.order.status, OrderStatus.PAID)

    def test_fee_updates_append_only_the_actual_difference(self):
        reconcile_payment(self.payment())
        data = self.payment(fee_details=[{"amount": 2, "fee_payer": "collector"}, {"amount": 10, "fee_payer": "payer"}], date_last_updated=(self.now+timedelta(minutes=1)).isoformat())
        reconcile_payment(data); reconcile_payment(data)
        fees = FinancialEntry.objects.filter(kind=FinancialEntryKind.TAXA_MERCADO_PAGO)
        self.assertEqual(fees.count(), 2)
        self.assertEqual(sum(fees.values_list("amount_cents_signed", flat=True)), -200)

    def test_recovery_search_fetches_and_validates_complete_payment(self):
        payment = self.payment()
        self.http.side_effect = [response({"results": [payment]}), response(payment)]
        refresh_charge(self.charge)
        self.assertEqual(self.http.call_count, 2)
        self.assertTrue(self.http.call_args.args[1].endswith("/v1/payments/998877"))
        self.order.refresh_from_db(); self.assertEqual(self.order.status, OrderStatus.PAID)

    def checkout_data(self, method="pix"):
        return {"payment_method": method, "attempt_key": signing.dumps({"order": str(self.order.id), "key": str(uuid.uuid4())}, salt="checkout-payment")}

    def test_checkout_get_cannot_create_a_charge(self):
        result = self.client.get(reverse("checkout:process_payment", args=[self.order.id]))
        self.assertEqual(result.status_code, 405)
        self.http.assert_not_called()

    def test_other_browser_cannot_read_or_charge_order_by_uuid(self):
        stranger = Client()
        for route in ["order_payment", "order_waiting", "order_result", "status_api"]:
            with self.subTest(route=route):
                self.assertEqual(stranger.get(reverse(f"checkout:{route}", args=[self.order.id])).status_code, 404)
        self.assertEqual(stranger.post(reverse("checkout:process_payment", args=[self.order.id]), self.checkout_data()).status_code, 404)
        self.http.assert_not_called()

    def test_pix_countdown_uses_persisted_expiry_and_never_confirms_on_expiration(self):
        self.charge.pix_expiration = self.now - timedelta(seconds=1)
        self.charge.pix_copy_paste = "SANDBOX_CODE"
        self.charge.save()
        data = self.client.get(reverse("checkout:status_api", args=[self.order.id])).json()
        self.assertEqual(data["pix_expires_at"], self.charge.pix_expiration.isoformat())
        self.assertIn("server_time", data)
        self.assertFalse(data["is_paid"])
        self.assertIsNone(data["redirect_url"])
        page = self.client.get(reverse("checkout:order_waiting", args=[self.order.id]))
        self.assertContains(page, 'id="pix-countdown"')
        self.assertNotContains(page, 'id="payment-success-title"')
        self.http.assert_not_called()

    def test_verified_pix_and_card_both_offer_success_modal_and_receipt(self):
        for method in ["pix", "credit_card"]:
            with self.subTest(method=method):
                self.charge.payment_method = method
                self.charge.save(update_fields=["payment_method"])
                reconcile_payment(self.payment(payment_method_id="master" if method == "credit_card" else "pix",
                    payment_type_id="credit_card" if method == "credit_card" else "bank_transfer"))
                data = self.client.get(reverse("checkout:status_api", args=[self.order.id])).json()
                self.assertTrue(data["is_paid"])
                self.assertEqual(data["redirect_url"], reverse("checkout:order_result", args=[self.order.id]))
                page = self.client.get(data["redirect_url"])
                self.assertContains(page, 'id="payment-success-title"')
                self.assertContains(page, reverse("minha_conta:registration_pdf", args=[self.registration.id]))
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_authenticated_buyer_can_access_order_without_guest_session(self):
        buyer = User.objects.create_user(email="owner@example.com", password="unit-password", full_name="Comprador")
        self.order.purchaser_user = buyer; self.order.save()
        browser = Client(); browser.force_login(buyer)
        response = browser.get(reverse("checkout:status_api", args=[self.order.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        other = User.objects.create_user(email="other@example.com", password="unit-password", full_name="Outro")
        browser.force_login(other)
        self.assertEqual(browser.get(reverse("checkout:status_api", args=[self.order.id])).status_code, 404)

    def test_namesakes_create_distinct_people_and_sequential_numbers(self):
        self.event.status = "PUBLISHED"; self.event.save()
        self.registration.registration_number = 7; self.registration.save()
        lot = Lot.objects.create(event=self.event, product=self.product, title="Lote", price_cents=18001,
            opens_at=self.now-timedelta(days=1), closes_at=self.now+timedelta(days=1), max_quantity=10)
        result = self.client.post(reverse("checkout:start_registration", args=[self.event.slug]), {
            "purchaser_name": "Comprador", "purchaser_email": "buyer@example.com", "purchaser_phone": "00000000000",
            "participant_document[]": ["52998224725", "11144477735"], "participant_name[]": ["Mesmo Nome", "Mesmo Nome"], "participant_email[]": ["first@example.com", "second@example.com"],
            "participant_product[]": [str(self.product.id), str(self.product.id)], "participant_lot[]": [str(lot.id), str(lot.id)],
        })
        self.assertEqual(result.status_code, 302)
        people = Person.objects.filter(full_name="Mesmo Nome")
        self.assertEqual(people.count(), 2)
        self.assertEqual(set(people.values_list("email", flat=True)), {"first@example.com", "second@example.com"})
        registrations = Registration.objects.filter(person__in=people).order_by("registration_number")
        self.assertEqual(list(registrations.values_list("registration_number", flat=True)), [8, 9])
        self.assertNotEqual(registrations[0].person_id, registrations[1].person_id)
        created_order = registrations[0].source_order_line.order
        self.assertIn(str(created_order.id), self.client.session["checkout_order_ids"])
        self.assertFalse(TermsAcceptance.objects.filter(registration__in=registrations).exists())
        self.assertEqual(CapacityHold.objects.filter(order_id=created_order.pk).count(), 4)
        self.assertEqual(CapacityResource.objects.get(type="LOT_TOTAL", reference_id=str(lot.id)).reserved_count, 2)

    def test_group_cannot_reserve_more_than_lot_capacity(self):
        self.event.status = "PUBLISHED"; self.event.save()
        lot = Lot.objects.create(event=self.event, product=self.product, title="Última vaga", price_cents=18001,
            opens_at=self.now-timedelta(days=1), closes_at=self.now+timedelta(days=1), max_quantity=1)
        before = Order.objects.count()
        result = self.client.post(reverse("checkout:start_registration", args=[self.event.slug]), {
            "purchaser_name": "Comprador", "purchaser_email": "buyer@example.com", "purchaser_phone": "00000000000",
            "participant_document[]": ["52998224725", "11144477735"], "participant_name[]": ["Primeiro", "Segundo"], "participant_product[]": [str(self.product.id)]*2,
            "participant_lot[]": [str(lot.id)]*2,
        })
        self.assertContains(result, "não tem vagas suficientes")
        self.assertEqual(Order.objects.count(), before)
        self.assertFalse(Person.objects.filter(full_name__in=["Primeiro", "Segundo"]).exists())
        self.http.assert_not_called()

    def test_invalid_lot_does_not_leave_partial_order(self):
        self.event.status = "PUBLISHED"; self.event.save()
        before = Order.objects.count()
        result = self.client.post(reverse("checkout:start_registration", args=[self.event.slug]), {
            "purchaser_name": "Comprador", "purchaser_email": "buyer@example.com", "purchaser_phone": "00000000000",
            "participant_document": "52998224725", "participant_name": "Inválido", "product_id": str(self.product.id), "lot_id": "not-a-uuid",
        })
        self.assertContains(result, "O pacote ou lote escolhido não está disponível")
        self.assertEqual(Order.objects.count(), before)

    def test_checkout_without_configuration_is_honest(self):
        self.charge.delete()
        with override_settings(MP_ACCESS_TOKEN=""):
            result = self.client.get(reverse("checkout:order_payment", args=[self.order.id]))
            self.assertContains(result, "Pagamento online indisponível")
            self.assertNotContains(result, "MOCK_TOKEN")
            self.client.post(reverse("checkout:process_payment", args=[self.order.id]), self.checkout_data())
        self.assertFalse(ChargeAttempt.objects.exists())
        self.http.assert_not_called()

    def test_unknown_charge_blocks_duplicate_submissions(self):
        self.charge.delete()
        self.http.side_effect = requests.Timeout()
        data = self.checkout_data()
        url = reverse("checkout:process_payment", args=[self.order.id])
        self.client.post(url, data)
        self.client.post(url, data)
        self.client.post(url, self.checkout_data("credit_card") | {"card_token": "test-token", "payment_method_id": "visa"})
        self.assertEqual(self.http.call_count, 1)
        self.assertEqual(ChargeAttempt.objects.count(), 1)
        self.assertEqual(ChargeAttempt.objects.get().state, ChargeState.UNKNOWN)
        self.assert_unpaid()

    def test_card_pending_response_is_not_displayed_as_paid(self):
        self.charge.delete()
        def provider(method, url, **kwargs):
            data = self.payment("in_process", metadata=kwargs["json"]["metadata"], payment_method_id="visa", payment_type_id="credit_card")
            return response(data, 201)
        self.http.side_effect = provider
        data = self.checkout_data("credit_card") | {"card_token": "test-token", "payment_method_id": "visa", "installments": "1"}
        result = self.client.post(reverse("checkout:process_payment", args=[self.order.id]), data)
        self.assertRedirects(result, reverse("checkout:order_waiting", args=[self.order.id]), fetch_redirect_response=False)
        self.assert_unpaid()
        self.assertEqual(ChargeAttempt.objects.get().state, ChargeState.PENDING)

    def test_pix_expiration_covers_provider_minimum_and_reserved_slot(self):
        self.charge.delete()
        self.order.expires_at = self.now+timedelta(minutes=3); self.order.save()
        def provider(method, url, **kwargs):
            return response(self.payment("pending", metadata=kwargs["json"]["metadata"]), 201)
        self.http.side_effect = provider
        self.client.post(reverse("checkout:process_payment", args=[self.order.id]), self.checkout_data())
        charge = ChargeAttempt.objects.get()
        self.order.refresh_from_db(); self.hold.refresh_from_db()
        self.assertGreater(charge.pix_expiration, timezone.now()+timedelta(minutes=30))
        self.assertGreater(self.order.expires_at, charge.pix_expiration)
        self.assertEqual(self.hold.expires_at, self.order.expires_at)

    def test_malformed_installments_are_rejected_without_gateway(self):
        self.charge.delete()
        result = self.client.post(reverse("checkout:process_payment", args=[self.order.id]),
            self.checkout_data("credit_card") | {"card_token": "test-token", "payment_method_id": "visa", "installments": "invalid"})
        self.assertEqual(result.status_code, 400)
        self.http.assert_not_called()

    def test_result_page_does_not_claim_unpaid_order_is_paid(self):
        result = self.client.get(reverse("checkout:order_result", args=[self.order.id]))
        self.assertContains(result, "Pagamento ainda não confirmado")
        self.assertNotContains(result, "O Mercado Pago confirmou o pagamento.")

    def test_card_form_contains_secure_iframes_and_no_raw_card_names(self):
        self.charge.delete()
        result = self.client.get(reverse("checkout:order_payment", args=[self.order.id]))
        self.assertContains(result, "iframe: true")
        self.assertContains(result, "https://sdk.mercadopago.com/js/v2")
        self.assertNotContains(result, 'name="card_number"')
        self.assertNotContains(result, 'name="security_code"')

    def test_zero_total_shows_organizer_confirmation_without_payment_forms(self):
        self.charge.delete()
        self.order.total_cents = 0; self.order.save()
        result = self.client.get(reverse("checkout:order_payment", args=[self.order.id]))
        self.assertContains(result, "Pedido sem cobrança")
        self.assertNotContains(result, 'id="pix-payment-form"')
        self.assertNotContains(result, "https://sdk.mercadopago.com/js/v2")
