import csv
import io
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.events.models import SponsorProfile,Sponsor,MotorcycleRaffle,RaffleTicket
from apps.finance.models import FinancialEntry
from apps.finance.sponsorships import record_sponsor_payment
from apps.reporting.exports import export_sponsorships_csv,export_raffle_csv
from apps.reporting.selectors import get_financial_summary
from tests import test_reporting_finance as fixtures


class ArchivedReportBalancesTests(TestCase):
    setUpTestData=classmethod(fixtures.ReportingFinanceTests.setUpTestData.__func__)

    def csv_rows(self,response):
        return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))

    def test_archived_sponsorship_export_matches_active_agreements_and_preserves_cash(self):
        profile=SponsorProfile.objects.create(organization=self.org,name='Marca de auditoria')
        sponsor=Sponsor.objects.create(event=self.event,profile=profile,name=profile.name,agreed_amount_cents=10000)
        record_sponsor_payment(event=self.event,sponsor_id=sponsor.pk,actor=self.user,data={
            'amount':3000,'received_on':timezone.localdate(),'method':'PIX','reference':'Recebimento fictício',
            'confirm_received':True,'idempotency_key':uuid.uuid4()})
        sponsor.archived_at=timezone.now();sponsor.save(update_fields=['archived_at'])
        totals=get_financial_summary(self.event)
        self.assertEqual(totals['sponsor_agreed_cents'],0)
        self.assertEqual(totals['sponsor_outstanding_cents'],0)
        self.assertEqual(totals['sponsor_received_cents'],3000)
        row=self.csv_rows(export_sponsorships_csv(self.event))[1]
        self.assertEqual(row[3],'0,00')
        self.assertEqual(row[4],'30,00')
        self.assertEqual(row[5],'0,00')
        self.assertIn('Excluído',row)
        self.assertIn('100,00',row)
        sponsor.archived_at=None;sponsor.save(update_fields=['archived_at'])
        row=self.csv_rows(export_sponsorships_csv(self.event))[1]
        self.assertEqual(row[3:6],['100,00','30,00','70,00'])

    def test_cancelled_paper_ticket_export_has_zero_net_receipt_and_original_value(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        ticket=RaffleTicket.objects.create(raffle=raffle,source='PAPER',number=5,state='VOID',buyer_name='Comprador fictício',amount_cents=1200,paid_at=timezone.now())
        FinancialEntry.objects.create(event=self.event,raffle=raffle,kind='RECEITA_SORTEIO',amount_cents_signed=1200,source_reference=f'RAFFLE-{ticket.pk}-SALE')
        FinancialEntry.objects.create(event=self.event,raffle=raffle,kind='ESTORNO_SORTEIO',amount_cents_signed=-1200,source_reference=f'RAFFLE-{ticket.pk}-VOID')
        self.assertEqual(get_financial_summary(self.event)['net_cents'],0)
        row=self.csv_rows(export_raffle_csv(self.event))[1]
        self.assertEqual(row[5],'0,00')
        self.assertIn('12,00',row)
        self.assertIn('Cancelado',row)
