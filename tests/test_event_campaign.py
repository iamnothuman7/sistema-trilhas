from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo
from django.core.management import call_command
from django.test import TestCase
from apps.events.models import EventTheme
from apps.core.models import AuditLog
from tests import test_system as fixtures

class EventCampaignTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def test_confirmed_calendar_command_is_scoped_dry_run_safe_and_idempotent(self):
        self.event.slug='3-trilhao-tiangua-off-road-2027'
        self.event.subdomain='3trilhaotiangua'
        self.event.starts_at=datetime(2027,3,20,6,tzinfo=ZoneInfo('America/Fortaleza'))
        self.event.ends_at=datetime(2027,3,21,18,tzinfo=ZoneInfo('America/Fortaleza'))
        self.event.description='Dias 20 e 21 de Março de 2027.'
        self.event.save()
        original_closes=self.event.registration_closes_at
        call_command('configure_third_trilhao_2027',stdout=StringIO())
        self.event.refresh_from_db();self.assertEqual(self.event.starts_at.day,20)
        call_command('configure_third_trilhao_2027',apply=True,stdout=StringIO())
        self.event.refresh_from_db()
        self.assertEqual(self.event.starts_at.day,19)
        self.assertEqual(self.event.ends_at.day,21)
        self.assertEqual(self.event.venue,'Estacionamento do Mix Mateus')
        self.assertEqual(self.event.registration_closes_at,original_closes)
        self.assertIn('19, 20 e 21',self.event.description)
        self.assertEqual(self.event.description.count('19,'),1)
        call_command('configure_third_trilhao_2027',apply=True,stdout=StringIO())
        self.assertEqual(AuditLog.objects.filter(action='EVENT_CONFIRMED_CALENDAR_UPDATED').count(),1)

    def test_event_covers_have_distinct_uses_and_custom_uploads_take_precedence(self):
        self.event.subdomain='3trilhaotiangua';self.event.save()
        theme=EventTheme.objects.get(event=self.event)
        self.assertTrue(theme.cover_url.endswith('3trilhao-cover-16x9.jpg'))
        self.assertTrue(theme.square_url.endswith('3trilhao-cover-square.webp'))
        theme.cover_image='events/covers/new-wide.jpg';theme.square_image='events/covers/new-square.png'
        self.assertTrue(theme.cover_url.endswith('new-wide.jpg'))
        self.assertTrue(theme.square_url.endswith('new-square.png'))
