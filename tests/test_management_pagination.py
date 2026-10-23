from datetime import timedelta
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine
from apps.core.models import AuditLog, Organization
from apps.events.models import Event
from apps.identity.models import EventMembership, Person, RoleChoices, User
from apps.registrations.models import CommercialState, OperationalState, Registration


class ManagementPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        org = Organization.objects.create(name="Organização da paginação")
        dates = dict(starts_at=now+timedelta(days=10), ends_at=now+timedelta(days=11),
                     registration_opens_at=now, registration_closes_at=now+timedelta(days=9))
        cls.event = Event.objects.create(organization=org, title="Edição autorizada", slug="paginacao-autorizada", **dates)
        cls.other = Event.objects.create(organization=org, title="Outra edição", slug="paginacao-outra", **dates)
        cls.manager = User.objects.create_user(email="paginacao@example.test", full_name="Gestão da edição")
        EventMembership.objects.create(user=cls.manager, event=cls.event, role_code=RoleChoices.EVENT_MANAGER)
        order = Order.objects.create(event=cls.event, merchant_reference="PAGINATION-OWN", purchaser_name="Compra da equipe",
                                     purchaser_email="comprador@example.test", expires_at=now+timedelta(hours=1))
        people = [Person(full_name=f"Grupo & Serra piloto {number:03d}") for number in range(1, 168)]
        Person.objects.bulk_create(people)
        lines = [OrderLine(order=order, intended_person_name=person.full_name) for person in people]
        OrderLine.objects.bulk_create(lines)
        registrations = [Registration(event=cls.event, person=person, source_order_line=line, registration_number=number,
                          commercial_state=CommercialState.ACTIVE, operational_state=OperationalState.READY)
                         for number, (person, line) in enumerate(zip(people, lines), 1)]
        registrations[-2].commercial_state = CommercialState.CANCELED
        registrations[-1].operational_state = OperationalState.NOT_READY
        Registration.objects.bulk_create(registrations)
        cls.registration_ids = {registration.pk for registration in registrations}
        other_order = Order.objects.create(event=cls.other, merchant_reference="PAGINATION-OTHER", purchaser_name="Outra compra",
                                           purchaser_email="other@example.test", expires_at=now+timedelta(hours=1))
        other_line = OrderLine.objects.create(order=other_order, intended_person_name="Participante de outra edição")
        Registration.objects.create(event=cls.other, person=people[0], source_order_line=other_line,
                                    registration_number=1, commercial_state=CommercialState.ACTIVE)
        logs = [AuditLog(event_id=cls.event.pk, action="EVENT_UPDATED", entity_type="Event", entity_id=str(cls.event.pk),
                        actor_email=cls.manager.email, reason=f"Atualização & revisão {number:03d}",
                        created_at=now+timedelta(seconds=number)) for number in range(123)]
        logs.extend([AuditLog(event_id=cls.event.pk, action="OTHER_ACTION", entity_type="Event", entity_id=str(cls.event.pk),
                             reason="Outro motivo", created_at=now+timedelta(minutes=10+number)) for number in range(2)])
        AuditLog.objects.bulk_create(logs)
        cls.audit_ids = {log.pk for log in logs}
        AuditLog.objects.create(event_id=cls.other.pk, action="EVENT_UPDATED", entity_type="Event", entity_id=str(cls.other.pk),
                                reason="Atividade restrita de outra edição")
        AuditLog.objects.create(action="GLOBAL_ACTION", entity_type="User", entity_id="global", reason="Atividade global restrita")

    def setUp(self):
        self.client.force_login(self.manager)
        self.registrations_url = reverse("gestao:registration_list", args=[self.event.pk])
        self.audit_url = reverse("gestao:audit_log")

    def pagination_links(self, response):
        urls = re.findall(r'href="([^\"]*)"', response.content.decode())
        return [query for url in urls if (query := parse_qs(urlsplit(unescape(url)).query)).get("page")]

    def test_all_registrations_beyond_150_are_reachable_without_duplicates(self):
        seen = []
        for number in range(1, 6):
            response = self.client.get(self.registrations_url, {"page": number})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["page_obj"].paginator.count, 167)
            self.assertEqual(len(response.context["registrations"]), 40 if number < 5 else 7)
            seen.extend(item.pk for item in response.context["registrations"])
        self.assertEqual(set(seen), self.registration_ids)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertContains(response, "Grupo &amp; Serra piloto 167")
        self.assertNotContains(response, "Grupo &amp; Serra piloto 001")
        self.assertIn("no-store", response["Cache-Control"])

    def test_registration_navigation_preserves_search_and_both_status_filters(self):
        filters = {"q": "Grupo & Serra", "status": "ACTIVE", "op_status": "READY"}
        response = self.client.get(self.registrations_url, filters)
        self.assertEqual(response.context["page_obj"].paginator.count, 165)
        links = self.pagination_links(response)
        self.assertTrue(links)
        for link in links:
            for key, value in filters.items():
                self.assertEqual(link[key], [value])
        last = self.client.get(self.registrations_url, filters | {"page": 5})
        self.assertEqual([reg.registration_number for reg in last.context["registrations"]], [161, 162, 163, 164, 165])

    def test_audit_history_beyond_100_is_reachable_and_event_scoped(self):
        seen = []
        for number in range(1, 5):
            response = self.client.get(self.audit_url, {"page": number})
            self.assertEqual(response.context["page_obj"].paginator.count, 125)
            self.assertEqual(len(response.context["logs"]), 40 if number < 4 else 5)
            seen.extend(item.pk for item in response.context["logs"])
            self.assertNotContains(response, "Atividade restrita de outra edição")
            self.assertNotContains(response, "Atividade global restrita")
        self.assertEqual(set(seen), self.audit_ids)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertIn("no-store", response["Cache-Control"])

    def test_audit_search_is_preserved_when_navigating(self):
        response = self.client.get(self.audit_url, {"q": "Atualização & revisão", "page": 2})
        self.assertEqual(response.context["page_obj"].paginator.count, 123)
        links = self.pagination_links(response)
        self.assertTrue(links)
        for link in links:
            self.assertEqual(link["q"], ["Atualização & revisão"])
        self.assertNotContains(response, "Outro motivo")

    def test_invalid_pages_and_empty_filters_are_safe(self):
        for url, count, last_page in ((self.registrations_url, 167, 5), (self.audit_url, 125, 4)):
            for raw, expected_page in (("invalid", 1), ("999999", last_page), ("0", last_page)):
                with self.subTest(url=url, page=raw):
                    response = self.client.get(url, {"page": raw})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["page_obj"].number, expected_page)
                    self.assertEqual(response.context["page_obj"].paginator.count, count)
            empty = self.client.get(url, {"q": "sem-correspondencia-exclusiva", "page": "999999"})
            self.assertEqual(empty.status_code, 200)
            self.assertEqual(empty.context["page_obj"].paginator.count, 0)
            self.assertEqual(empty.context["page_obj"].number, 1)
            self.assertContains(empty, "0 registros")
            self.assertNotContains(empty, 'rel="next"')

    def test_pagination_does_not_bypass_role_or_other_event_permissions(self):
        self.assertEqual(self.client.get(reverse("gestao:registration_list", args=[self.other.pk]), {"page": 2}).status_code, 403)
        EventMembership.objects.filter(user=self.manager).update(role_code=RoleChoices.FINANCE)
        for url in (self.registrations_url, self.audit_url):
            self.assertEqual(self.client.get(url, {"page": 2}).status_code, 403)
