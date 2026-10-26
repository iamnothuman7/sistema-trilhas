from django.test import TestCase
from django.urls import reverse
from apps.identity.models import User, EventMembership, RoleChoices
from tests import test_system as fixtures


class UsageGuideTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def test_guide_is_internal(self):
        url = reverse('gestao:usage_guide')
        self.assertEqual(self.client.get(url).status_code, 302)
        participant = User.objects.create_user(email='guide-participant@example.test')
        self.client.force_login(participant)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_operator_can_read_guide_but_only_gets_authorized_links(self):
        operator = User.objects.create_user(email='guide-operator@example.test')
        EventMembership.objects.create(user=operator, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.client.force_login(operator)
        response = self.client.get(reverse('gestao:usage_guide'))
        self.assertContains(response, 'O caminho do participante')
        self.assertContains(response, reverse('gestao:credenciamento', kwargs={'event_id': self.event.id}))
        self.assertNotContains(response, reverse('gestao:finance', kwargs={'event_id': self.event.id}))
        self.assertNotContains(response, reverse('gestao:mercadopago_connection'))
        self.assertIn('no-store', response['Cache-Control'])

    def test_administrator_has_event_shortcuts_and_sidebar_entry(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('gestao:usage_guide'), {'event_id': self.event.id})
        self.assertContains(response, reverse('gestao:reports', kwargs={'event_id': self.event.id}))
        self.assertContains(response, 'usage-guide.css')
        self.assertContains(response, 'input-masks.js')
        self.assertContains(self.client.get('/gestao/'), 'Como usar')

    def test_guide_works_without_an_event(self):
        # A new organization can open the guide before creating an event.
        self.client.force_login(self.user)
        from unittest.mock import patch
        from apps.events.models import Event
        with patch('apps.core.views_gestao.accessible_events', return_value=Event.objects.none()), patch('apps.core.permissions.accessible_events', return_value=Event.objects.none()):
            response = self.client.get(reverse('gestao:usage_guide'))
        self.assertContains(response, 'Nenhum evento disponível')
