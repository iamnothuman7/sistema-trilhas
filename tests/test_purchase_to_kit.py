"""Whole journey with a simulated, authenticated provider response; no real charge."""
from urllib.parse import urlsplit
import uuid
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.events.models import LegalDocumentVersion, DocumentKind
from apps.identity.models import User, EventMembership, RoleChoices
from apps.payments.services import reconcile_payment
from apps.registrations.models import Consumption
from tests import test_payments as payment_fixtures


@override_settings(**payment_fixtures.MP_TEST_SETTINGS)
class PurchaseToKitJourneyTests(TestCase):
    setUp = payment_fixtures.PaymentReconciliationTests.setUp
    payment = payment_fixtures.PaymentReconciliationTests.payment

    def test_confirmed_purchase_account_terms_pdf_checkin_and_single_kit_delivery(self):
        document = LegalDocumentVersion.objects.create(event=self.event, kind=DocumentKind.REGULAMENTO,
            version_number=1, content="Regulamento de teste do percurso e retirada.")
        reconcile_payment(self.payment())
        access = reverse("identity:purchase_access", args=[self.order.pk])
        result = self.client.post(access, {"new_password1": "Trilhas-Journey-2027", "new_password2": "Trilhas-Journey-2027"})
        self.assertEqual(result.status_code, 302)
        share = self.client.post(reverse("minha_conta:create_claim", args=[self.registration.pk]))
        claim_path = urlsplit(share.context["claim_url"]).path
        self.assertEqual(self.client.post(claim_path, {"confirm_holder": "yes"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("minha_conta:accept_terms", args=[self.registration.pk]),
            {"document_version_id": [str(document.pk)]}).status_code, 302)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.operational_state, "READY")
        receipt = self.client.get(reverse("minha_conta:registration_pdf", args=[self.registration.pk]))
        self.assertEqual(receipt.status_code, 200)
        self.assertTrue(receipt.content.startswith(b"%PDF-"))
        operator = User.objects.create_user(email="kit-operator@example.test", full_name="Entrega local")
        EventMembership.objects.create(user=operator, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.client.force_login(operator)
        prefix = f"/gestao/eventos/{self.event.pk}/credenciamento/"
        found = self.client.get(prefix+"lookup-api/", {"code": self.registration.credential.raw_token}).json()
        self.assertTrue(found["eligible"])
        self.assertEqual(self.client.post(prefix+"confirm-api/", {"registration_id": self.registration.pk}).status_code, 200)
        entitlement = self.registration.entitlements.get(benefit_type="KIT")
        delivery = {"entitlement_id": entitlement.pk, "quantity": 1, "idempotency_key": uuid.uuid4()}
        self.assertEqual(self.client.post(prefix+"deliver-benefit-api/", delivery).status_code, 200)
        self.assertEqual(self.client.post(prefix+"deliver-benefit-api/", delivery).status_code, 200)
        entitlement.refresh_from_db()
        self.assertEqual(entitlement.quantity_used, 1)
        self.assertEqual(Consumption.objects.filter(entitlement=entitlement).count(), 1)
