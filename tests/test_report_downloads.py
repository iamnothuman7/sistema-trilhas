import csv
import io
import zipfile
from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.identity.models import User, EventMembership, RoleChoices
from apps.finance.models import Expense
from apps.reporting.exports import AGGREGATE_REPORTS
from tests import test_system as fixtures


class ReportDownloadTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def url(self): return reverse('gestao:reports',kwargs={'event_id':self.event.pk})

    def test_all_exports_download_and_zip_includes_every_available_report(self):
        self.client.force_login(self.user)
        Expense.objects.create(event=self.event, description='=SUM(A1)', amount_cents=1500, due_at=timezone.localdate())
        names = {'participants','orders','financial','summary','expenses','checkins','benefits','deliveries','sponsorships','sponsor_receipts','extras','raffle', *AGGREGATE_REPORTS}
        contents = {}
        for name in names:
            response = self.client.get(self.url(), {'export':name})
            self.assertEqual(response.status_code,200,name)
            self.assertIn('attachment',response['Content-Disposition'])
            self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
            contents[name]=response.content
        response = self.client.get(self.url(), {'export':'all'})
        self.assertIn('no-store',response['Cache-Control'])
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            self.assertEqual(set(archive.namelist()), {name+'.csv' for name in names}|{'LEIA-ME.txt'})
            for name in names: self.assertEqual(archive.read(name+'.csv'),contents[name])
        self.assertIn("'=SUM(A1)",contents['expenses'].decode())

    def test_finance_role_cannot_export_restricted_individual_operations(self):
        user = User.objects.create_user(email='report-limited@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code=RoleChoices.FINANCE)
        self.client.force_login(user)
        for name in ['participants','checkins','benefits','deliveries']:
            self.assertEqual(self.client.get(self.url(),{'export':name}).status_code,403)
        response=self.client.get(self.url(),{'export':'all'})
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            for name in ['participants','checkins','benefits','deliveries']: self.assertNotIn(name+'.csv',archive.namelist())

    def test_dates_are_applied_to_expenses_and_bundle(self):
        self.client.force_login(self.user)
        today=timezone.localdate()
        Expense.objects.create(event=self.event,description='DENTRO',amount_cents=1500,due_at=today)
        Expense.objects.create(event=self.event,description='FORA',amount_cents=1500,due_at=today-timedelta(days=1))
        response=self.client.get(self.url(),{'export':'all','date_start':str(today),'date_end':str(today)})
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            data=archive.read('expenses.csv').decode()
            self.assertIn('DENTRO',data);self.assertNotIn('FORA',data)
        self.assertEqual(self.client.get(self.url(),{'export':'all','date_start':'invalid'}).status_code,400)
