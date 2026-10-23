"""Operator workflows: explicit selection, eligibility and atomic partial delivery."""
from datetime import timedelta
import uuid
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.events.models import Event, EventStatus, LegalDocumentVersion, DocumentKind
from apps.identity.models import Person, UserPersonLink, EventMembership, RoleChoices
from apps.operations.models import CheckinRecord, CheckinType
from apps.registrations.models import (
    Registration, RegistrationCredential, TermsAcceptance, CommercialState,
    OperationalState, Entitlement, EntitlementState, Consumption,
)
from tests import test_system as system_fixtures
from tests import test_management_access as management_fixtures


class ScannerOperationsTests(TestCase):
    def setUp(self):
        system_fixtures.SistemaTrilhasTests.setUp(self)
        self.operator = management_fixtures.ManagementAccessTests.operator(self)
        self.reg = self.make_registration()
        self.ent = Entitlement.objects.create(registration=self.reg, benefit_type="KIT", title="Kit de teste", quantity_total=3)
        prefix = f"/gestao/eventos/{self.event.pk}/credenciamento/"
        self.lookup = prefix + "lookup-api/"
        self.confirm = prefix + "confirm-api/"
        self.deliver = prefix + "deliver-benefit-api/"

    def make_registration(self, name="Piloto Exemplo", number=42, event=None):
        event = event or self.event
        token = uuid.uuid4().hex
        person = Person.objects.create(full_name=name, city="Tianguá", email=f"{token}@example.test")
        order = Order.objects.create(event=event, merchant_reference=token, purchaser_name=name,
            purchaser_email=person.email, total_cents=12000, status=OrderStatus.PAID, expires_at=timezone.now()+timedelta(hours=1))
        line = OrderLine.objects.create(order=order, product=self.product, lot=self.lot, intended_person_name=name, unit_cents=12000, net_cents=12000)
        reg = Registration.objects.create(event=event, person=person, source_order_line=line,
            registration_number=number, commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.READY)
        UserPersonLink.objects.create(user=self.user, person=person)
        document, _ = LegalDocumentVersion.objects.get_or_create(event=event, kind=DocumentKind.REGULAMENTO,
            version_number=1, defaults={"content": "Regulamento de teste"})
        TermsAcceptance.objects.create(registration=reg, document_version=document, accepting_user=self.user, evidence_hash="0" * 64)
        RegistrationCredential.objects.create(registration=reg, raw_token=token, token_digest=token * 2)
        return reg

    def delivery_payload(self, quantity=1, key=None):
        return {"entitlement_id": self.ent.pk, "idempotency_key": key or uuid.uuid4(), "quantity": quantity}

    def test_qr_number_name_and_selected_id_lookup_do_not_mutate(self):
        for params in ({"code": self.reg.credential.raw_token}, {"code": "#42"}, {"code": "Piloto Exemplo"},
                       {"code": f"TRILHA:{self.event.slug}:{self.reg.credential.raw_token}"}, {"registration_id": str(self.reg.pk)}):
            response = self.client.get(self.lookup, params)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertTrue(response.json()["eligible"])
            self.assertEqual(response.json()["registration_id"], str(self.reg.pk))
            self.assertNotIn("email", response.json())
            self.assertEqual(response.json()["entitlements"][0]["remaining"], 3)
        self.assertFalse(CheckinRecord.objects.exists())
        self.assertFalse(Consumption.objects.exists())
        self.assertFalse(self.client.get(self.lookup, {"code": f"TRILHA:other:{self.reg.credential.raw_token}"}).json()["found"])
        self.assertFalse(self.client.get(self.lookup, {"code": "999999999999999999999"}).json()["found"])
        self.assertEqual(self.client.get(self.lookup, {"code": "P"}).status_code, 400)

    def test_homonyms_require_selection_and_limit_results(self):
        for number in range(43, 54):
            self.make_registration(number=number)
        data = self.client.get(self.lookup, {"code": "Piloto"}).json()
        self.assertFalse(data["found"])
        self.assertTrue(data["ambiguous"])
        self.assertTrue(data["more_results"])
        self.assertEqual(len(data["matches"]), 10)
        selected = data["matches"][2]["registration_id"]
        self.assertEqual(self.client.get(self.lookup, {"registration_id": selected}).json()["registration_id"], selected)
        self.assertFalse(CheckinRecord.objects.exists())
        self.assertFalse(Consumption.objects.exists())

    def test_lookup_and_operations_cannot_cross_editions_or_roles(self):
        other = Event.objects.create(organization=self.org, title="Outra edição", slug="outro-scanner",
            starts_at=self.event.starts_at, ends_at=self.event.ends_at,
            registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        reg = self.make_registration(event=other, number=42)
        ent = Entitlement.objects.create(registration=reg, benefit_type="KIT", title="Kit de outra edição")
        self.assertFalse(self.client.get(self.lookup, {"registration_id": str(reg.pk)}).json()["found"])
        self.assertFalse(self.client.get(self.lookup, {"code": reg.credential.raw_token}).json()["found"])
        self.assertEqual(self.client.post(self.confirm, {"registration_id": reg.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.deliver, {"entitlement_id": ent.pk, "idempotency_key": uuid.uuid4()}).status_code, 404)
        EventMembership.objects.filter(user=self.operator).update(role_code=RoleChoices.CONTENT)
        self.assertEqual(self.client.get(self.lookup, {"code": "42"}).status_code, 403)
        self.assertEqual(self.client.post(self.confirm, {"registration_id": self.reg.pk}).status_code, 403)
        self.assertEqual(self.client.post(self.deliver, self.delivery_payload()).status_code, 403)

    def test_all_current_eligibility_gates_apply_to_manual_lookup_and_actions(self):
        mutations = [
            (lambda: Registration.objects.filter(pk=self.reg.pk).update(commercial_state=CommercialState.CANCELED),
             lambda: Registration.objects.filter(pk=self.reg.pk).update(commercial_state=CommercialState.ACTIVE)),
            (lambda: Registration.objects.filter(pk=self.reg.pk).update(operational_state=OperationalState.NOT_READY),
             lambda: Registration.objects.filter(pk=self.reg.pk).update(operational_state=OperationalState.READY)),
            (lambda: Order.objects.filter(pk=self.reg.source_order_line.order_id).update(status=OrderStatus.RESERVED),
             lambda: Order.objects.filter(pk=self.reg.source_order_line.order_id).update(status=OrderStatus.PAID)),
            (lambda: RegistrationCredential.objects.filter(registration=self.reg).update(revoked_at=timezone.now()),
             lambda: RegistrationCredential.objects.filter(registration=self.reg).update(revoked_at=None)),
            (lambda: RegistrationCredential.objects.filter(registration=self.reg).update(expires_at=timezone.now()-timedelta(seconds=1)),
             lambda: RegistrationCredential.objects.filter(registration=self.reg).update(expires_at=None)),
            (lambda: Event.objects.filter(pk=self.event.pk).update(status=EventStatus.CANCELED),
             lambda: Event.objects.filter(pk=self.event.pk).update(status=EventStatus.PUBLISHED)),
            (lambda: Event.objects.filter(pk=self.event.pk).update(status=EventStatus.ARCHIVED),
             lambda: Event.objects.filter(pk=self.event.pk).update(status=EventStatus.PUBLISHED)),
        ]
        for block, restore in mutations:
            block()
            data = self.client.get(self.lookup, {"code": "42"}).json()
            self.assertTrue(data["found"])
            self.assertFalse(data["eligible"])
            self.assertTrue(data["blocked_reason"])
            self.assertEqual(self.client.post(self.confirm, {"registration_id": self.reg.pk}).status_code, 409)
            self.assertEqual(self.client.post(self.deliver, self.delivery_payload()).status_code, 409)
            restore()
        self.assertFalse(CheckinRecord.objects.exists())
        self.assertFalse(Consumption.objects.exists())

    def test_new_terms_missing_holder_acceptance_or_credential_block_operations(self):
        doc = LegalDocumentVersion.objects.create(event=self.event, kind=DocumentKind.REGULAMENTO, version_number=2, content="Nova regra")
        self.assertEqual(self.client.post(self.confirm, {"registration_id": self.reg.pk}).status_code, 409)
        self.assertEqual(self.client.post(self.deliver, self.delivery_payload()).status_code, 409)
        TermsAcceptance.objects.create(registration=self.reg, document_version=doc, accepting_user=self.operator, evidence_hash="1"*64)
        self.assertEqual(self.client.post(self.confirm, {"registration_id": self.reg.pk}).status_code, 409)
        TermsAcceptance.objects.create(registration=self.reg, document_version=doc, accepting_user=self.user, evidence_hash="2"*64)
        RegistrationCredential.objects.filter(registration=self.reg).delete()
        self.assertFalse(self.client.get(self.lookup, {"code": "42"}).json()["eligible"])
        self.assertEqual(self.client.post(self.confirm, {"registration_id": self.reg.pk}).status_code, 409)
        self.assertEqual(self.client.post(self.deliver, self.delivery_payload()).status_code, 409)

    def test_finished_event_retains_eligible_pickup_and_presence_is_idempotent(self):
        Event.objects.filter(pk=self.event.pk).update(status=EventStatus.FINISHED)
        payload = {"registration_id": self.reg.pk, "lookup_method": "manual"}
        self.assertFalse(self.client.post(self.confirm, payload).json()["already_checked_in"])
        self.assertTrue(self.client.post(self.confirm, payload).json()["already_checked_in"])
        self.assertEqual(CheckinRecord.objects.get().checkin_type, CheckinType.MANUAL_SEARCH)
        self.assertTrue(self.client.post(self.deliver, self.delivery_payload()).json()["success"])
        reg = self.make_registration(number=43)
        self.client.post(self.confirm, {"registration_id": reg.pk, "lookup_method": "qr"})
        self.assertEqual(CheckinRecord.objects.get(registration=reg).checkin_type, CheckinType.SCAN)

    def test_partial_quantity_replay_history_and_final_balance(self):
        payload = self.delivery_payload(quantity=2)
        first = self.client.post(self.deliver, payload).json()
        self.assertEqual(first["remaining"], 1)
        self.assertFalse(first["already_delivered"])
        self.assertTrue(self.client.post(self.deliver, payload).json()["already_delivered"])
        self.ent.refresh_from_db()
        self.assertEqual(self.ent.state, EntitlementState.PARTIALLY_USED)
        self.assertEqual(self.ent.quantity_used, 2)
        self.assertEqual(Consumption.objects.get().quantity, 2)
        self.assertEqual(self.client.post(self.deliver, {**payload, "quantity": 1}).status_code, 409)
        self.assertEqual(self.client.post(self.deliver, self.delivery_payload(quantity=2)).status_code, 409)
        final = self.client.post(self.deliver, self.delivery_payload()).json()
        self.assertEqual(final["remaining"], 0)
        self.assertTrue(final["is_consumed"])
        self.assertTrue(self.client.post(self.deliver, payload).json()["already_delivered"])
        self.assertEqual(Consumption.objects.count(), 2)
        history = self.client.get(self.lookup, {"code": "42"}).json()["delivery_history"]
        self.assertEqual([item["quantity"] for item in history], [1, 2])
        self.assertEqual(history[0]["operator"], self.operator.full_name)
        self.assertEqual(history[0]["title"], self.ent.title)

    def test_invalid_quantity_csrf_and_http_methods_never_mutate(self):
        for quantity in (0, -1, 1001, "1.2", "oops"):
            self.assertEqual(self.client.post(self.deliver, self.delivery_payload(quantity=quantity)).status_code, 400)
        self.assertEqual(self.client.get(self.deliver).status_code, 405)
        self.assertEqual(self.client.get(self.confirm).status_code, 405)
        self.assertEqual(self.client.post(self.lookup, {"code": "42"}).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.operator)
        self.assertEqual(strict.post(self.deliver, self.delivery_payload()).status_code, 403)
        self.assertFalse(Consumption.objects.exists())
        self.assertFalse(CheckinRecord.objects.exists())

    def test_delivery_rolls_back_history_if_balance_save_fails(self):
        with patch("apps.registrations.models.Entitlement.save", side_effect=RuntimeError("storage unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.deliver, self.delivery_payload(quantity=2))
        self.ent.refresh_from_db()
        self.assertEqual(self.ent.quantity_used, 0)
        self.assertFalse(Consumption.objects.exists())

    def test_camera_template_loads_local_decoder_and_requires_explicit_start(self):
        response = self.client.get(f"/gestao/eventos/{self.event.pk}/credenciamento/")
        self.assertContains(response, 'vendor/jsqr/jsQR.js')
        self.assertContains(response, 'id="btnStartCamera"')
        self.assertContains(response, 'id="scannerPhoto"')
        self.assertNotContains(response, "scannerApp.startCamera()")
        self.assertIn("no-store", response["Cache-Control"])
