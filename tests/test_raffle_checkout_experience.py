"""Buyer guidance and complete exports for confirmed motorcycle draw entries."""
import re
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.checkout.models import Order, OrderLine, OrderStatus
from apps.core.models import AuditLog
from apps.events.models import LegalDocumentVersion
from apps.events.models_raffles import RaffleTicket
from apps.events.raffle_pdf import build_tickets_pdf
from apps.payments.services import activate_registration_if_eligible
from apps.registrations.models import TermsAcceptance
from tests import test_payments as payment_fixtures
from tests import test_raffle_online_payments as raffle_fixtures


@override_settings(**payment_fixtures.MP_TEST_SETTINGS)
class RaffleCheckoutExperienceTests(TestCase):
    setUp = raffle_fixtures.OnlineRaffleTests.setUp
    payment = raffle_fixtures.OnlineRaffleTests.payment
    prepare = raffle_fixtures.OnlineRaffleTests.prepare
    reserve = raffle_fixtures.OnlineRaffleTests.reserve
    approve = raffle_fixtures.OnlineRaffleTests.approve

    def test_paid_extra_numbers_stay_confirmed_while_terms_are_pending(self):
        self.prepare()
        order = self.reserve()
        self.approve(order)
        response = self.client.get(reverse('checkout:order_result', args=[order.pk]))
        self.assertTrue(response.context['is_paid'])
        self.assertFalse(response.context['registrations'][0].download_ready)
        self.assertContains(response, 'Seus números estão confirmados!')
        self.assertContains(response, 'Seus números já estão garantidos.')
        self.assertContains(response, 'Concluir pendências e liberar QR')
        self.assertContains(response, reverse('minha_conta:registration_detail', args=[self.registration.pk]))
        self.assertContains(response, 'Baixar PDF com meus números')
        self.assertNotContains(response, 'Ver meu cartão com QR')
        self.assertNotContains(response, 'Salvar cartão como imagem')
        pdf = self.client.get(reverse('minha_conta:registration_pdf', args=[self.registration.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF-'))

    def test_ready_participant_gets_qr_pdf_and_image_actions_after_extra_purchase(self):
        self.prepare()
        document = LegalDocumentVersion.objects.create(event=self.event, kind='REGULAMENTO', version_number=1, content='Regras de teste')
        TermsAcceptance.objects.create(registration=self.registration, document_version=document,
            accepting_user=self.user, evidence_hash='a' * 64)
        self.assertTrue(activate_registration_if_eligible(self.registration))
        order = self.reserve()
        self.approve(order)
        response = self.client.get(reverse('checkout:order_result', args=[order.pk]))
        self.assertTrue(response.context['registrations'][0].download_ready)
        self.assertContains(response, 'Ver meu cartão com QR')
        self.assertContains(response, 'Baixar PDF com meus números')
        self.assertContains(response, 'Salvar cartão como imagem')
        self.assertNotContains(response, 'Concluir pendências e liberar QR')

    def test_payment_summary_identifies_extra_numbers_and_exact_quantity_price_and_subtotal(self):
        self.prepare()
        order = self.reserve(quantity=3)
        response = self.client.get(reverse('checkout:order_payment', args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Números extras do sorteio · Pagamento')
        self.assertContains(response, '3 números extras × R$ 5,00 por unidade')
        self.assertContains(response, 'Subtotal: R$ 15,00')
        self.assertNotContains(response, 'Sua inscrição · Pagamento')

    def test_extra_purchase_draw_time_follows_its_event_timezone(self):
        self.prepare()
        self.event.timezone = 'America/Manaus'
        self.event.save(update_fields=['timezone'])
        expected = timezone.localtime(self.raffle.draw_at, ZoneInfo('America/Manaus')).strftime('%d/%m/%Y %H:%M')
        server_time = timezone.localtime(self.raffle.draw_at, ZoneInfo('America/Fortaleza')).strftime('%d/%m/%Y %H:%M')
        self.assertNotEqual(expected, server_time)
        response = self.client.get(reverse('checkout:raffle_numbers', args=[self.registration.pk]))
        self.assertContains(response, f'Data prevista: {expected}.')
        self.assertNotContains(response, f'Data prevista: {server_time}.')

    def test_globe_export_never_silently_omits_confirmed_entries_over_ten_thousand(self):
        self.prepare()
        # Model fixtures represent 501 already settled purchases of up to 20
        # extras each; this exercise concerns export completeness, not checkout.
        quantities = [20] * 500 + [1]
        orders = [Order(event=self.event, purchaser_user=self.user, purchaser_name=self.user.full_name,
            purchaser_email=self.user.email, purchaser_phone='', merchant_reference=f'VOLUME-QA-{index}',
            status=OrderStatus.PAID, expires_at=self.order.expires_at,
            gross_cents=quantity * 500, total_cents=quantity * 500)
            for index, quantity in enumerate(quantities)]
        Order.objects.bulk_create(orders, batch_size=100)
        lines = [OrderLine(order=order, raffle=self.raffle, linked_registration_id=self.registration.pk,
            quantity=quantity, unit_cents=500, net_cents=quantity * 500, intended_person_name=self.person.full_name)
            for order, quantity in zip(orders, quantities)]
        OrderLine.objects.bulk_create(lines, batch_size=100)
        RaffleTicket.objects.bulk_create([
            RaffleTicket(raffle=self.raffle, number=10000 + index, source='ONLINE', state='SOLD',
                order_line=lines[index // 20], beneficiary_registration=self.registration,
                buyer_name=self.person.full_name, amount_cents=500)
            for index in range(10001)
        ], batch_size=1000)
        expected = self.raffle.tickets.filter(state='SOLD').count()
        self.assertEqual(expected, 10002)  # Included registration entry + extras.
        with patch('apps.events.raffle_pdf.build_tickets_pdf', return_value=b'%PDF-QA') as build:
            response = self.client.get(reverse('gestao:raffle_print', args=[self.event.pk]) + '?formato=globo')
        self.assertEqual(response.status_code, 200)
        sent = build.call_args.args[1]
        self.assertEqual(len(sent), expected)
        self.assertEqual(sent[-1].number, 20000)
        self.assertEqual([ticket.number for ticket in sent], sorted(ticket.number for ticket in sent))
        audit = AuditLog.objects.get(action='RAFFLE_TICKETS_EXPORTED')
        self.assertEqual(audit.diff_json['ticket_count'], expected)


class RafflePDFPaginationTests(SimpleTestCase):
    def test_globe_generator_adds_pages_for_every_entry(self):
        raffle = SimpleNamespace(event=SimpleNamespace(title='Sorteio de teste'))
        tickets = [SimpleNamespace(number=number, source='ONLINE') for number in range(1, 82)]
        content = build_tickets_pdf(raffle, tickets, globo=True)
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertEqual(len(re.findall(rb'/Type\s*/Page(?:\s|/|>)', content)), 3)
