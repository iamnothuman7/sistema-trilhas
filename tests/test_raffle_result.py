from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from apps.events.models import MotorcycleRaffle,RaffleTicket,RaffleResult
from apps.events.raffle_results import eligible_tickets,record_draw_result
from apps.events.raffle_operations import create_paper_batch,record_paper_ticket
from tests import test_system as system_fixtures


class RaffleResultTests(TestCase):
    def setUp(self):
        system_fixtures.SistemaTrilhasTests.setUp(self);self.client.force_login(self.user)
        self.raffle=MotorcycleRaffle.objects.create(event=self.event,draw_at=timezone.now()-timedelta(minutes=1),rules_text='Apuração por globo físico conferida pela organização.')
        self.ticket=RaffleTicket.objects.create(raffle=self.raffle,number=2000,source='PAPER',state='SOLD',paid_at=timezone.now(),amount_cents=500,buyer_name='Pessoa de teste')

    def record(self,**changes):
        data=dict(raffle=self.raffle,actor=self.user,numbers=[2000],notes='Apuração fictícia por globo, conferida.',confirmed=True)
        data.update(changes);return record_draw_result(**data)

    def test_result_freezes_eligible_snapshot_idempotently_and_stops_new_sales(self):
        result,created=self.record();self.assertTrue(created);self.assertFalse(self.record()[1])
        self.assertEqual(RaffleResult.objects.count(),1);self.assertEqual(len(result.eligible_snapshot),1)
        with self.assertRaises(ValidationError):self.record(numbers=[2001])
        self.raffle.refresh_from_db();self.assertFalse(self.raffle.purchase_available)
        with self.assertRaises(ValidationError):create_paper_batch(self.raffle,self.user)
        self.ticket.state='VOID';self.ticket.save()
        self.assertEqual(eligible_tickets(self.raffle).count(),0)
        result.refresh_from_db();self.assertEqual(result.winners[0]['number'],2000)
        self.assertEqual(self.client.get(reverse('gestao:raffle_result',args=[self.event.pk])).status_code,200)

    def test_pending_wrong_event_void_numbers_and_future_draw_rejected(self):
        for state in ['AVAILABLE','DISTRIBUTED','VOID']:
            self.ticket.state=state;self.ticket.save()
            with self.assertRaises(ValidationError):self.record()
        self.ticket.state='SOLD';self.ticket.save()
        with self.assertRaises(ValidationError):self.record(numbers=[9999])
        self.raffle.draw_at=timezone.now()+timedelta(days=1);self.raffle.save()
        with self.assertRaises(ValidationError):self.record()
        self.assertFalse(RaffleResult.objects.exists())

    def test_no_new_paper_receipt_after_result(self):
        self.record()
        ticket=RaffleTicket.objects.create(raffle=self.raffle,number=2001,source='PAPER')
        with self.assertRaises(ValidationError):record_paper_ticket(ticket_id=ticket.pk,actor=self.user,point=None,
            buyer_name='Fictício',buyer_phone='',amount_cents=500,confirmed=True)
