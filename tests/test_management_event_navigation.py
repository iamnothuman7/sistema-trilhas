from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.management_navigation import MODULES
from apps.core.models import AuditLog
from apps.events.models import Event, Sponsor
from apps.identity.models import User, EventMembership, RoleChoices
from tests import test_system as fixtures


class ManagementEventNavigationTests(TestCase):
    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.other = Event.objects.create(organization=self.org, title='Encontro da Serra', slug='encontro-serra',
            starts_at=self.event.starts_at, ends_at=self.event.ends_at,
            registration_opens_at=self.event.registration_opens_at,
            registration_closes_at=self.event.registration_closes_at)
        self.client.force_login(self.user)

    def test_switch_stays_in_each_workspace_and_remembers_selection(self):
        for module, (_, scoped) in MODULES.items():
            with self.subTest(module=module):
                response = self.client.get(reverse('gestao:switch_event'), {'module': module, 'event_id': self.other.pk})
                expected = reverse(f'gestao:{module}', kwargs={'event_id': self.other.pk}) if scoped else reverse(f'gestao:{module}') + f'?event_id={self.other.pk}'
                self.assertRedirects(response, expected, fetch_redirect_response=False)
                self.assertEqual(self.client.session['management_event_id'], str(self.other.pk))
        home = self.client.get(reverse('gestao:dashboard'))
        self.assertEqual(home.context['selected_event'], self.other)

    def test_catalog_switch_does_not_mix_products_and_sidebar_follows(self):
        Product.objects.create(event=self.other, name='Pacote exclusivo da Serra')
        first = self.client.get(reverse('gestao:catalog', args=[self.event.pk]))
        self.assertContains(first, 'id="management-event-select"')
        self.assertNotContains(first, 'Pacote exclusivo da Serra')
        other = self.client.get(reverse('gestao:switch_event'), {'module': 'catalog', 'event_id': self.other.pk}, follow=True)
        self.assertContains(other, 'Pacote exclusivo da Serra')
        self.assertNotContains(other, 'Editar lote Lote 1')
        self.assertContains(other, reverse('gestao:registration_list', args=[self.other.pk]))
        self.assertEqual(self.client.get(reverse('gestao:dashboard')).context['selected_event'], self.other)

    def test_record_editors_switch_to_parent_without_reusing_old_ids(self):
        response = self.client.get(reverse('gestao:catalog_edit', kwargs={
            'event_id': self.event.pk, 'kind': 'lote', 'object_id': self.lot.pk}))
        self.assertEqual(response.context['management_switch_module'], 'catalog')

    def test_switch_rejects_unknown_destination_invalid_ids_and_wrong_roles(self):
        endpoint = reverse('gestao:switch_event')
        for data in ({'module': '//example.com', 'event_id': self.other.pk},
                     {'module': 'finance', 'event_id': 'bad'}, {'module': 'finance', 'event_id': 'all'}):
            self.assertEqual(self.client.get(endpoint, data).status_code, 404)
        operator = User.objects.create_user(email='switch-scope@example.test')
        EventMembership.objects.create(user=operator, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.client.force_login(operator)
        self.assertEqual(self.client.get(endpoint, {'module': 'credenciamento', 'event_id': self.other.pk}).status_code, 403)
        self.assertEqual(self.client.get(endpoint, {'module': 'finance', 'event_id': self.event.pk}).status_code, 403)
        response = self.client.get(reverse('gestao:credenciamento', args=[self.event.pk]))
        self.assertNotContains(response, self.other.title)
        self.assertEqual(self.client.post(endpoint, {'module': 'credenciamento', 'event_id': self.event.pk}).status_code, 405)

    def test_audit_event_filter_and_all_events(self):
        AuditLog.objects.create(event_id=self.event.pk, action='FIRST_EVENT', entity_type='Event', entity_id=str(self.event.pk))
        AuditLog.objects.create(event_id=self.other.pk, action='SECOND_EVENT', entity_type='Event', entity_id=str(self.other.pk))
        url = reverse('gestao:audit_log')
        scoped = self.client.get(url, {'event_id': self.other.pk})
        self.assertContains(scoped, 'SECOND_EVENT')
        self.assertNotContains(scoped, 'FIRST_EVENT')
        self.assertEqual(scoped.context['audit_event'], self.other)
        both = self.client.get(url, {'event_id': 'all'})
        self.assertContains(both, 'FIRST_EVENT')
        self.assertContains(both, 'SECOND_EVENT')
        self.assertTrue(both.context['management_all_selected'])

    def test_sponsor_all_scope_is_explicit_and_selected_event_scopes_data(self):
        Sponsor.objects.create(event=self.event, name='Marca do primeiro evento')
        Sponsor.objects.create(event=self.other, name='Marca do segundo evento')
        url = reverse('gestao:sponsor_bank')
        scoped = self.client.get(url, {'event_id': self.other.pk})
        self.assertContains(scoped, 'Marca do segundo evento')
        self.assertNotContains(scoped, 'Marca do primeiro evento')
        both = self.client.get(url, {'event_id': 'all'})
        self.assertContains(both, 'Marca do primeiro evento')
        self.assertContains(both, 'Marca do segundo evento')
        self.assertTrue(both.context['management_all_selected'])

    def test_removed_logistics_has_no_links_or_accessible_endpoint(self):
        for url in [reverse('gestao:dashboard'), reverse('gestao:event_detail', args=[self.event.pk]), reverse('gestao:usage_guide')]:
            self.assertNotContains(self.client.get(url), 'Logística')
        self.assertEqual(self.client.get(f'/gestao/eventos/{self.event.pk}/expedicao/').status_code, 404)
