from django.test import TestCase, override_settings
from tests import test_system as _fixtures
from apps.events.models import EventStatus


@override_settings(ALLOWED_HOSTS=['testserver', '.equipemorcego.nabio.pro'])
class PublicEventScopeTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def test_unknown_slug_does_not_show_another_event(self):
        for suffix in ('', 'programacao/', 'percurso/', 'regulamento/', 'patrocinadores/', 'molduras/'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get('/eventos/evento-inexistente/' + suffix).status_code, 404)

    def test_draft_is_hidden_from_public_and_previewable_by_manager(self):
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=['status'])
        url = f'/eventos/{self.event.slug}/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_unknown_event_subdomain_returns_404(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='trilhao-inexistente.equipemorcego.nabio.pro').status_code, 404)

    def test_exact_event_subdomain_resolves(self):
        response = self.client.get('/', HTTP_HOST=f'{self.event.subdomain}.equipemorcego.nabio.pro')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['event'], self.event)

    def test_draft_subdomain_is_hidden(self):
        self.event.status = EventStatus.DRAFT
        self.event.save(update_fields=['status'])
        self.assertEqual(self.client.get('/', HTTP_HOST=f'{self.event.subdomain}.equipemorcego.nabio.pro').status_code, 404)
