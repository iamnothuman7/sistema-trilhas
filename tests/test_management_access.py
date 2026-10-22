from datetime import timedelta
import uuid
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from apps.identity.models import User, EventMembership, RoleChoices, Person
from apps.events.models import Event
from apps.checkout.models import Order, OrderLine
from apps.registrations.models import Registration, CommercialState, OperationalState, Entitlement, EntitlementState, Consumption
from apps.operations.models import CheckinRecord
from tests import test_system as _fixtures


class ManagementAccessTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def operator(self, role=RoleChoices.CHECKIN_AGENT):
        user = User.objects.create_user(email="operator@example.test", password="local-only-test", full_name="Operação")
        EventMembership.objects.create(user=user, event=self.event, role_code=role)
        self.client.force_login(user)
        return user

    def registration(self, commercial=CommercialState.ACTIVE, operational=OperationalState.READY):
        person = Person.objects.create(full_name="Piloto Teste", email="piloto@example.test")
        order = Order.objects.create(event=self.event, merchant_reference="ACCESS-1", purchaser_name="Teste", purchaser_email="teste@example.test", total_cents=18000, expires_at=timezone.now()+timedelta(hours=1))
        line = OrderLine.objects.create(order=order, product=self.product, lot=self.lot, intended_person_name="Piloto Teste", unit_cents=18000, net_cents=18000)
        return Registration.objects.create(event=self.event, person=person, source_order_line=line, registration_number=42, commercial_state=commercial, operational_state=operational)

    def test_operator_cannot_access_finance_or_export(self):
        self.operator()
        for suffix in ("financeiro/", "exportar/financeiro/", "relatorios/"):
            self.assertEqual(self.client.get(f"/gestao/eventos/{self.event.id}/{suffix}").status_code, 403)

    def test_operator_cannot_cross_events(self):
        self.operator()
        other = Event.objects.create(organization=self.org, title="Outro evento", slug="outro", starts_at=timezone.now(), ends_at=timezone.now()+timedelta(days=1), registration_opens_at=timezone.now(), registration_closes_at=timezone.now()+timedelta(days=1))
        response = self.client.get(f"/gestao/eventos/{other.id}/credenciamento/")
        self.assertEqual(response.status_code, 403)
        dashboard = self.client.get(f"/gestao/?event_id={other.id}")
        self.assertEqual(dashboard.context['selected_event'], self.event)

    def test_revoked_membership_cannot_access_management(self):
        user = self.operator()
        EventMembership.objects.filter(user=user).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get('/gestao/').status_code,403)

    def test_finance_role_has_no_catalog_permission(self):
        self.operator(RoleChoices.FINANCE)
        self.assertEqual(self.client.get(f"/gestao/eventos/{self.event.id}/catalogo/").status_code,403)

    def test_duplicate_requires_post(self):
        self.client.force_login(self.user)
        count=Event.objects.count()
        self.assertEqual(self.client.get(f"/gestao/eventos/{self.event.id}/duplicar/").status_code,405)
        self.assertEqual(Event.objects.count(),count)

    def test_event_scoped_manager_cannot_duplicate_or_see_create_actions(self):
        self.operator(RoleChoices.EVENT_MANAGER)
        count = Event.objects.count()
        duplicate_url = f"/gestao/eventos/{self.event.id}/duplicar/"
        self.assertEqual(self.client.post(duplicate_url).status_code, 403)
        self.assertEqual(Event.objects.count(), count)
        for url in ("/gestao/", "/gestao/eventos/", f"/gestao/eventos/{self.event.id}/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'href="/gestao/eventos/novo/"')
            self.assertNotContains(response, f'action="{duplicate_url}"')

    def test_global_manager_can_duplicate_and_access_created_edition(self):
        user = self.operator(RoleChoices.EVENT_MANAGER)
        EventMembership.objects.filter(user=user).update(event=None)
        response = self.client.post(f"/gestao/eventos/{self.event.id}/duplicar/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_unpaid_or_not_ready_cannot_check_in(self):
        self.operator()
        reg=self.registration(commercial=CommercialState.RESERVED)
        url=f"/gestao/eventos/{self.event.id}/credenciamento/confirm-api/"
        self.assertEqual(self.client.post(url,{'registration_id':reg.id}).status_code,409)
        reg.commercial_state=CommercialState.ACTIVE
        reg.operational_state=OperationalState.NOT_READY
        reg.save()
        self.assertEqual(self.client.post(url,{'registration_id':reg.id}).status_code,409)
        self.assertFalse(CheckinRecord.objects.exists())

    def test_eligible_checkin_is_idempotent(self):
        self.operator()
        reg=self.registration()
        url=f"/gestao/eventos/{self.event.id}/credenciamento/confirm-api/"
        self.assertTrue(self.client.post(url,{'registration_id':reg.id}).json()['success'])
        self.assertTrue(self.client.post(url,{'registration_id':reg.id}).json()['already_checked_in'])
        self.assertEqual(CheckinRecord.objects.count(),1)

    def test_revoked_benefit_cannot_be_delivered(self):
        self.operator()
        reg=self.registration()
        ent=Entitlement.objects.create(registration=reg,benefit_type='KIT',title='Kit',state=EntitlementState.REVOKED)
        response=self.client.post(f"/gestao/eventos/{self.event.id}/credenciamento/deliver-benefit-api/",{'entitlement_id':ent.id, 'idempotency_key': uuid.uuid4()})
        self.assertEqual(response.status_code,409)
        ent.refresh_from_db()
        self.assertEqual(ent.quantity_used,0)

    def test_lookup_by_name_and_registration_search(self):
        self.operator(RoleChoices.REGISTRATION_AGENT)
        self.registration()
        response=self.client.get(f"/gestao/eventos/{self.event.id}/credenciamento/lookup-api/",{'code':'Piloto Teste'})
        self.assertTrue(response.json()['found'])
        self.assertEqual(self.client.get(f"/gestao/eventos/{self.event.id}/inscricoes/",{'q':'Piloto Teste'}).status_code,200)

    def test_invalid_selected_event_does_not_raise(self):
        self.operator()
        self.assertEqual(self.client.get('/gestao/?event_id=not-a-uuid').status_code,200)

    def test_registration_status_change_rolls_back_when_audit_fails(self):
        self.operator(RoleChoices.REGISTRATION_AGENT)
        reg = self.registration()
        with patch("apps.core.views_gestao.log_audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(f"/gestao/eventos/{self.event.id}/inscricoes/{reg.id}/status/",
                                 {"commercial_state": CommercialState.CANCELED, "reason": "Solicitação do participante"})
        reg.refresh_from_db()
        self.assertEqual(reg.commercial_state, CommercialState.ACTIVE)

    def test_cancellation_preserves_history_and_blocks_checkin_and_delivery(self):
        self.operator(RoleChoices.REGISTRATION_AGENT)
        reg = self.registration()
        ent = Entitlement.objects.create(registration=reg, benefit_type="KIT", title="Kit", quantity_total=2)
        checkin_url = f"/gestao/eventos/{self.event.id}/credenciamento/confirm-api/"
        delivery_url = f"/gestao/eventos/{self.event.id}/credenciamento/deliver-benefit-api/"
        self.assertTrue(self.client.post(checkin_url, {"registration_id": reg.pk}).json()["success"])
        self.assertTrue(self.client.post(delivery_url, {"entitlement_id": ent.pk, "idempotency_key": uuid.uuid4()}).json()["success"])
        response = self.client.post(f"/gestao/eventos/{self.event.id}/inscricoes/{reg.id}/status/",
                                    {"commercial_state": CommercialState.CANCELED, "reason": "Solicitação do participante"})
        self.assertEqual(response.status_code, 302)
        reg.refresh_from_db()
        self.assertEqual(reg.operational_state, OperationalState.CHECKED_IN)
        self.assertEqual(self.client.post(checkin_url, {"registration_id": reg.pk}).status_code, 409)
        self.assertEqual(self.client.post(delivery_url, {"entitlement_id": ent.pk, "idempotency_key": uuid.uuid4()}).status_code, 409)
        self.assertEqual(CheckinRecord.objects.filter(registration=reg).count(), 1)
        self.assertEqual(Consumption.objects.filter(entitlement=ent).count(), 1)
        ent.refresh_from_db()
        self.assertEqual(ent.quantity_used, 1)

    def test_delivery_retries_consume_one_unit_and_new_intention_consumes_next(self):
        self.operator()
        reg = self.registration()
        ent = Entitlement.objects.create(registration=reg, benefit_type="KIT", title="Dois kits", quantity_total=2)
        url = f"/gestao/eventos/{self.event.id}/credenciamento/deliver-benefit-api/"
        first_intention = {"entitlement_id": ent.pk, "idempotency_key": uuid.uuid4()}
        self.assertFalse(self.client.post(url, first_intention).json()["already_delivered"])
        self.assertTrue(self.client.post(url, first_intention).json()["already_delivered"])
        ent.refresh_from_db()
        self.assertEqual(ent.quantity_used, 1)
        self.assertEqual(Consumption.objects.filter(entitlement=ent).count(), 1)
        self.assertFalse(self.client.post(url, {"entitlement_id": ent.pk, "idempotency_key": uuid.uuid4()}).json()["already_delivered"])
        # Replays still succeed after the balance reaches zero, without another consumption.
        self.assertTrue(self.client.post(url, first_intention).json()["already_delivered"])
        ent.refresh_from_db()
        self.assertEqual(ent.quantity_used, 2)
        self.assertEqual(Consumption.objects.filter(entitlement=ent).count(), 2)

    def test_delivery_requires_valid_idempotency_key_before_mutation(self):
        self.operator()
        reg = self.registration()
        ent = Entitlement.objects.create(registration=reg, benefit_type="KIT", title="Kit")
        url = f"/gestao/eventos/{self.event.id}/credenciamento/deliver-benefit-api/"
        for key in ("", "not-a-uuid"):
            self.assertEqual(self.client.post(url, {"entitlement_id": ent.pk, "idempotency_key": key}).status_code, 400)
        self.assertFalse(Consumption.objects.exists())
        ent.refresh_from_db()
        self.assertEqual(ent.quantity_used, 0)
