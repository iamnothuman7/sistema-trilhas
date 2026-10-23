"""No external requests or real credentials: all account responses are mocked."""
from datetime import timedelta
import json
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order
from apps.core.models import AuditLog, Organization
from apps.events.models import Event
from apps.identity.models import User, EventMembership, RoleChoices
from apps.payments.gateway import MercadoPagoGateway
from apps.payments.gateway_config import decrypt_configuration, encrypt_configuration, CredentialStorageError
from apps.payments.models import MercadoPagoConnection, ChargeAttempt


EMPTY_SETTINGS = dict(MP_ACCESS_TOKEN="", MP_PUBLIC_KEY="", MP_WEBHOOK_SECRET="", MP_COLLECTOR_ID="", MP_ENVIRONMENT="sandbox",
    MP_WEBHOOK_URL="https://equipemorcego.nabio.pro/integracoes/mercadopago/webhook/", MP_API_BASE="https://api.mercadopago.com",
    MP_CREDENTIALS_ENCRYPTION_KEY="", SECRET_KEY="connection-tests-isolated-secret-key-not-for-production")


@override_settings(**EMPTY_SETTINGS)
class MercadoPagoConnectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(email="owner-connection@example.test", full_name="Proprietário de teste")
        EventMembership.objects.create(user=cls.owner, role_code=RoleChoices.OWNER, event=None)
        org = Organization.objects.create(name="Equipe teste")
        now = timezone.now()
        cls.event = Event.objects.create(organization=org, title="Evento teste", slug="conexao-teste", starts_at=now,
            ends_at=now+timedelta(days=1), registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(hours=1))

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse("gestao:mercadopago_connection")
        self.http = patch("apps.payments.gateway.requests.request").start()
        self.addCleanup(patch.stopall)
        self.http.side_effect = AssertionError("Unexpected external request")
        self.values = dict(access_token="TEST-connection-private-token", public_key="TEST-connection-public-key",
            webhook_secret="unit-webhook-secret-only", collector_id="1234", environment="sandbox")

    def current_version(self):
        connection = MercadoPagoConnection.objects.filter(pk=1).first()
        return connection.revision if connection else 0

    def save_draft(self, **overrides):
        return self.client.post(self.url, {"action": "save_draft", "version": self.current_version(), **self.values, **overrides})

    def verify(self, account_id=1234):
        self.http.side_effect = None
        self.http.return_value = Mock(status_code=200)
        self.http.return_value.json.return_value = {"id": account_id, "email": "private-account@example.test", "first_name": "Private"}
        return self.client.post(self.url, {"action": "verify", "version": self.current_version()})

    def activate(self, **overrides):
        return self.client.post(self.url, {"action": "activate", "version": self.current_version(),
            "confirm_account": "on", "confirm_environment": "on", "confirm_webhook": "on", **overrides})

    def test_get_is_read_only_no_network_and_no_secret_in_page(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertFalse(MercadoPagoConnection.objects.exists())
        self.http.assert_not_called()
        self.assertContains(response, "Testar conta sem cobrar")
        self.assertContains(response, "Pagamentos online indisponíveis")

    def test_only_global_owner_and_superuser_can_write(self):
        for role, global_scope, staff, can_read in ((RoleChoices.OWNER, False, False, True),
            (RoleChoices.EVENT_MANAGER, False, False, True), (RoleChoices.EVENT_MANAGER, True, False, True),
            (RoleChoices.FINANCE, False, False, True), (None, False, True, False), (None, False, False, False)):
            user = User.objects.create_user(email=f"restricted-{User.objects.count()}@example.test", is_staff=staff)
            if role:
                EventMembership.objects.create(user=user, role_code=role, event=None if global_scope else self.event)
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200 if can_read else 403)
            if can_read:
                self.assertNotContains(response, 'name="access_token"')
            for action in ("save_draft", "verify", "activate"):
                self.assertEqual(self.client.post(self.url, {"action": action, "version": 0, **self.values}).status_code, 403)
        self.assertFalse(MercadoPagoConnection.objects.exists())
        admin = User.objects.create_user(email="super-connection@example.test", is_superuser=True, is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.save_draft().status_code, 302)

    def test_revoked_owner_and_missing_csrf_cannot_change_connection(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(strict.post(self.url, {"action": "save_draft", "version": 0, **self.values}).status_code, 403)
        EventMembership.objects.filter(user=self.owner).update(revoked_at=timezone.now())
        self.assertEqual(self.save_draft().status_code, 403)
        self.assertFalse(MercadoPagoConnection.objects.exists())

    def test_draft_is_encrypted_never_rendered_or_logged_and_does_not_activate(self):
        self.assertEqual(self.save_draft().status_code, 302)
        connection = MercadoPagoConnection.objects.get()
        decoded = decrypt_configuration(connection.draft_ciphertext)
        self.assertEqual(decoded["access_token"], self.values["access_token"])
        self.assertFalse(connection.active_ciphertext)
        self.assertTrue(MercadoPagoGateway().configuration_issues())
        html = self.client.get(self.url).content.decode()
        audits = json.dumps(list(AuditLog.objects.values("action", "diff_json")))
        for secret in (self.values["access_token"], self.values["webhook_secret"], self.values["public_key"]):
            self.assertNotIn(secret, connection.draft_ciphertext)
            self.assertNotIn(secret, html)
            self.assertNotIn(secret, audits)
        self.http.assert_not_called()

    def test_blank_fields_preserve_credentials_and_save_invalidates_account_check(self):
        self.save_draft()
        self.verify()
        self.assertEqual(self.save_draft(access_token="", public_key="", webhook_secret="").status_code, 302)
        connection = MercadoPagoConnection.objects.get()
        self.assertIsNone(connection.verified_at)
        self.assertFalse(connection.verified_draft_digest)
        self.assertEqual(decrypt_configuration(connection.draft_ciphertext)["access_token"], self.values["access_token"])
        self.assertEqual(self.activate().status_code, 409)

    def test_invalid_form_does_not_echo_submitted_secrets_or_change_version(self):
        response = self.save_draft(collector_id="not-a-number")
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, self.values["access_token"], status_code=400)
        self.assertNotContains(response, self.values["webhook_secret"], status_code=400)
        self.assertFalse(MercadoPagoConnection.objects.exists())
        self.assertEqual(self.client.post(self.url, {"action": "save_draft", **self.values}).status_code, 400)

    def test_incomplete_draft_does_not_replace_valid_environment_or_send_request(self):
        with override_settings(MP_ACCESS_TOKEN="ENV-private", MP_PUBLIC_KEY="ENV-public", MP_WEBHOOK_SECRET="ENV-webhook", MP_COLLECTOR_ID="5678"):
            self.assertEqual(self.save_draft(collector_id="").status_code, 302)
            self.assertEqual(MercadoPagoGateway().collector_id, "5678")
            self.assertEqual(MercadoPagoGateway().access_token, "ENV-private")
            response = self.client.post(self.url, {"action": "verify", "version": self.current_version()})
            self.assertEqual(response.status_code, 409)
            self.http.assert_not_called()

    def test_test_button_only_gets_account_with_timeout_and_confirms_expected_id(self):
        self.save_draft()
        self.assertEqual(self.verify().status_code, 302)
        args, kwargs = self.http.call_args
        self.assertEqual(args, ("GET", "https://api.mercadopago.com/users/me"))
        self.assertEqual(kwargs["timeout"], (5, 20))
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertIsNone(kwargs["json"])
        connection = MercadoPagoConnection.objects.get()
        self.assertIsNotNone(connection.verified_at)
        self.assertEqual(connection.verified_collector_id, "1234")
        self.assertFalse(connection.active_ciphertext)
        self.assertNotIn("private-account", json.dumps(list(AuditLog.objects.values("diff_json"))))
        self.assertFalse(ChargeAttempt.objects.exists())

    def test_wrong_receiver_clears_verification_without_exposing_provider_details(self):
        self.save_draft()
        self.verify()
        response = self.verify(account_id=9999)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "outra conta", status_code=409)
        self.assertNotContains(response, "private-account@example.test", status_code=409)
        self.assertIsNone(MercadoPagoConnection.objects.get().verified_at)
        self.assertEqual(self.activate().status_code, 409)

    def test_network_and_provider_errors_are_sanitized_and_never_activate(self):
        self.save_draft()
        for status in (401, 500, 302):
            self.http.side_effect = None
            self.http.return_value = Mock(status_code=status)
            self.http.return_value.json.return_value = {"message": self.values["access_token"]}
            response = self.client.post(self.url, {"action": "verify", "version": self.current_version()})
            self.assertEqual(response.status_code, 409)
            self.assertNotContains(response, self.values["access_token"], status_code=409)
        self.http.side_effect = requests.Timeout(self.values["access_token"])
        response = self.client.post(self.url, {"action": "verify", "version": self.current_version()})
        self.assertEqual(response.status_code, 409)
        self.assertNotContains(response, self.values["access_token"], status_code=409)
        self.assertFalse(MercadoPagoConnection.objects.get().active_ciphertext)

    def test_active_snapshot_is_used_by_gateway_and_draft_changes_do_not_replace_it(self):
        self.save_draft()
        self.verify()
        self.assertEqual(self.activate().status_code, 302)
        gateway = MercadoPagoGateway()
        self.assertEqual(gateway.configuration_source, "panel")
        self.assertEqual(gateway.access_token, self.values["access_token"])
        self.assertEqual(gateway.configuration_issues(card=True), [])
        self.save_draft(access_token="TEST-new-draft-token", collector_id="9999")
        self.assertEqual(MercadoPagoGateway().access_token, self.values["access_token"])
        self.assertEqual(MercadoPagoGateway().collector_id, "1234")

    def test_activation_requires_confirmation_and_production_requires_homologation(self):
        self.save_draft(environment="production", access_token="APP_USR-unit-private")
        self.verify()
        self.assertEqual(self.activate(confirm_account="").status_code, 400)
        self.assertEqual(self.activate().status_code, 409)
        self.assertFalse(MercadoPagoConnection.objects.get().active_ciphertext)
        self.assertEqual(self.activate(confirm_sandbox="on").status_code, 302)
        self.assertEqual(MercadoPagoGateway().environment, "production")

    def test_expired_verification_or_changed_ciphertext_cannot_activate(self):
        self.save_draft()
        self.verify()
        MercadoPagoConnection.objects.update(verified_at=timezone.now()-timedelta(minutes=31))
        self.assertEqual(self.activate().status_code, 409)
        self.verify()
        connection = MercadoPagoConnection.objects.get()
        values = decrypt_configuration(connection.draft_ciphertext)
        values["collector_id"] = "9999"
        MercadoPagoConnection.objects.update(draft_ciphertext=encrypt_configuration(values))
        self.assertEqual(self.activate().status_code, 409)

    def test_stale_tab_cannot_overwrite_draft_or_activate_newer_revision(self):
        self.save_draft()
        old = self.current_version()
        self.save_draft(collector_id="5678")
        self.assertEqual(self.client.post(self.url, {"action": "save_draft", "version": old, **self.values}).status_code, 409)
        self.assertEqual(decrypt_configuration(MercadoPagoConnection.objects.get().draft_ciphertext)["collector_id"], "5678")
        self.verify(account_id=5678)
        self.assertEqual(self.activate(version=old).status_code, 409)

    def test_account_response_for_concurrently_edited_draft_cannot_verify_it(self):
        self.save_draft()
        def concurrent_change(*args, **kwargs):
            connection = MercadoPagoConnection.objects.get()
            connection.revision += 1
            connection.save()
            result = Mock(status_code=200)
            result.json.return_value = {"id": 1234}
            return result
        self.http.side_effect = concurrent_change
        self.assertEqual(self.client.post(self.url, {"action": "verify", "version": self.current_version()}).status_code, 409)
        self.assertIsNone(MercadoPagoConnection.objects.get().verified_at)

    def test_changing_encryption_key_fails_closed_without_using_environment_account(self):
        self.save_draft()
        self.verify()
        self.activate()
        with override_settings(MP_CREDENTIALS_ENCRYPTION_KEY="different-key-for-isolated-test", MP_ACCESS_TOKEN="fallback-secret", MP_COLLECTOR_ID="9999"):
            gateway = MercadoPagoGateway()
            self.assertEqual(gateway.configuration_source, "unavailable")
            self.assertEqual(gateway.access_token, "")
            self.assertTrue(gateway.configuration_issues())
            response = self.client.get(self.url)
            self.assertContains(response, "chave do servidor")
            self.assertNotContains(response, "fallback-secret")
            with self.assertRaises(CredentialStorageError):
                decrypt_configuration(MercadoPagoConnection.objects.get().active_ciphertext)

    def test_existing_charge_blocks_receiver_or_environment_switch_but_not_token_rotation(self):
        self.save_draft()
        self.verify()
        self.activate()
        order = Order.objects.create(event=self.event, merchant_reference="CONNECTION-HISTORY", purchaser_name="Comprador teste",
            purchaser_email="buyer@example.test", total_cents=12000, expires_at=timezone.now()+timedelta(hours=1))
        ChargeAttempt.objects.create(order=order, amount_cents=12000, idempotency_key="history-connection")
        self.save_draft(collector_id="5678")
        self.verify(account_id=5678)
        self.assertEqual(self.activate().status_code, 409)
        self.assertEqual(MercadoPagoGateway().collector_id, "1234")
        self.save_draft(access_token="TEST-rotated-own-token")
        self.verify()
        self.assertEqual(self.activate().status_code, 302)
        self.assertEqual(MercadoPagoGateway().access_token, "TEST-rotated-own-token")

    def test_verified_configuration_can_change_before_first_charge(self):
        self.save_draft()
        self.verify()
        self.activate()
        for changed in ({"collector_id": "5678"}, {"environment": "production"},
                        {"public_key": "another-application-public"}, {"webhook_secret": "another-webhook-secret"}):
            self.save_draft(**changed)
            self.verify(account_id=int(changed.get("collector_id", "1234")))
            self.assertEqual(self.activate(confirm_sandbox="on").status_code, 302)
        self.assertEqual(MercadoPagoGateway().collector_id, "1234")
        self.assertFalse(ChargeAttempt.objects.exists())

    def test_environment_can_be_replaced_before_first_charge_with_explicit_activation(self):
        with override_settings(MP_ACCESS_TOKEN="ENV-private", MP_PUBLIC_KEY="ENV-public", MP_WEBHOOK_SECRET="ENV-webhook", MP_COLLECTOR_ID="5678"):
            self.save_draft()
            self.verify()
            self.assertEqual(self.activate().status_code, 302)
            self.assertEqual(MercadoPagoGateway().collector_id, "1234")

    def test_audit_failure_rolls_back_connection_change(self):
        with patch("apps.payments.connection.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.save_draft()
        self.assertFalse(MercadoPagoConnection.objects.exists())
