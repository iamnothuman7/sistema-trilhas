import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import MotorcycleRaffle, RaffleTicket, Sponsor
from apps.finance.entry_actions import attach_entry_actions
from apps.finance.forms import expense_version
from apps.finance.ledger_display import operational_financial_entries, mark_ledger_history
from apps.finance.models import ExpenseStatus, FinancialEntry, FinancialEntryKind
from apps.finance.movements import save_movement, archive_movement
from apps.finance.services import update_expense, void_paid_expense, settle_expense
from apps.finance.sponsorships import record_sponsor_payment, reverse_sponsor_payment
from apps.reporting.exports import export_financial_csv
from apps.reporting.selectors import financial_entries, get_financial_summary
from tests import test_finance_crud as fixtures


class OperationalLedgerTests(TestCase):
    setUpTestData = classmethod(fixtures.FinanceCrudTests.setUpTestData.__func__)
    setUp = fixtures.FinanceCrudTests.setUp
    payload = fixtures.FinanceCrudTests.payload
    new = fixtures.FinanceCrudTests.new
    create = fixtures.FinanceCrudTests.create
    expense_payload = fixtures.FinanceCrudTests.expense_payload

    def rows(self, start=None, end=None):
        return list(operational_financial_entries(self.event, start, end))

    def archive(self, row, restore=False):
        row.refresh_from_db()
        archive_movement(event=self.event, movement_id=row.pk, revision=row.revision,
                         actor=self.user, reason='Conferência do registro de teste', restore=restore)

    def test_removed_manual_is_only_in_history_and_restoration_shows_one_current_post(self):
        row = self.new(description='Entrada para conferir exclusão')
        self.assertEqual(len(self.rows()), 1)
        self.archive(row)
        self.assertEqual(self.rows(), [])
        self.assertEqual(financial_entries(self.event).count(), 2)
        self.assertEqual(get_financial_summary(self.event)['net_cents'], 0)
        exported = export_financial_csv(self.event).content.decode('utf-8-sig')
        self.assertIn('Correção / exclusão: Entrada para conferir exclusão', exported)
        self.archive(row, restore=True)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0].amount_cents_signed, 12050)
        self.assertEqual(financial_entries(self.event).count(), 3)
        self.assertEqual(get_financial_summary(self.event)['net_cents'], 12050)

    def test_manual_correction_uses_latest_post_globally_before_period_filter(self):
        yesterday = self.today - timedelta(days=1)
        row = self.new(occurred_on=yesterday.isoformat())
        from apps.finance.views_crud import MovementForm
        form = MovementForm(self.payload(occurred_on=self.today.isoformat(), amount_cents='95,75',
                            revision=row.revision, reason='Corrigir competência e valor'), movement=row)
        self.assertTrue(form.is_valid(), form.errors)
        save_movement(event=self.event, movement_id=row.pk, data=form.cleaned_data, actor=self.user)
        self.assertEqual(self.rows(yesterday, yesterday), [])
        self.assertEqual([x.amount_cents_signed for x in self.rows(self.today, self.today)], [9575])
        self.assertEqual(get_financial_summary(self.event, yesterday, yesterday)['net_cents'], 0)
        self.assertEqual(get_financial_summary(self.event, self.today, self.today)['net_cents'], 9575)

    def test_expense_correction_cancel_restore_and_new_payment_keep_history_intact(self):
        expense = self.create(amount='150,00')
        update_expense(event=self.event, expense_id=expense.pk, actor=self.user, data={
            'version': expense_version(expense), 'description': expense.description,
            'category': expense.category, 'amount': 12500, 'vendor_id': self.vendor,
            'due_at': self.today, 'paid_at': self.today, 'status': 'PAID',
            'reason': 'Corrigir o valor pago no teste',
        })
        self.assertEqual([x.amount_cents_signed for x in self.rows()], [-12500])
        self.assertEqual(financial_entries(self.event).count(), 3)
        expense.refresh_from_db()
        void_paid_expense(event=self.event, expense_id=expense.pk, actor=self.user,
                          version=expense_version(expense), reason='Anular despesa fictícia registrada por engano', confirmed=True)
        self.assertEqual(self.rows(), [])
        self.assertEqual(get_financial_summary(self.event)['net_cents'], 0)
        expense.refresh_from_db(); expense.status = ExpenseStatus.PENDING; expense.paid_at = None
        expense.save(update_fields=['status', 'paid_at'])
        self.assertEqual(self.rows(), [])
        settle_expense(event=self.event, expense_id=expense.pk, actor=self.user, paid_at=self.today)
        self.assertEqual([x.amount_cents_signed for x in self.rows()], [-12500])
        self.assertEqual(financial_entries(self.event).count(), 5)
        self.assertEqual(get_financial_summary(self.event)['net_cents'], -12500)

    def test_reversed_sponsor_payment_hidden_but_archived_sponsor_real_receipt_retained(self):
        sponsor = Sponsor.objects.create(event=self.event, name='Patrocinador fictício', agreed_amount_cents=50000)
        def pay(amount):
            return record_sponsor_payment(event=self.event, sponsor_id=sponsor.pk, actor=self.user, data={
                'amount': amount, 'received_on': self.today, 'method': 'PIX', 'reference': '', 'notes': '',
                'confirm_received': True, 'idempotency_key': uuid.uuid4(),
            })[0]
        payment = pay(10000)
        reverse_sponsor_payment(event=self.event, sponsor_id=sponsor.pk, payment_id=payment.pk,
                                actor=self.user, reason='Reverter recebimento fictício')
        self.assertEqual(self.rows(), [])
        active = pay(15000)
        sponsor.archived_at = timezone.now(); sponsor.save(update_fields=['archived_at'])
        self.assertEqual([x.pk for x in self.rows()], [active.ledger_entry_id])
        self.assertEqual(get_financial_summary(self.event)['net_cents'], 15000)

    def test_void_paper_ticket_removed_but_unknown_sources_and_other_event_isolation_preserved(self):
        raffle = MotorcycleRaffle.objects.create(event=self.event)
        ticket = RaffleTicket.objects.create(raffle=raffle, number=2000, source='PAPER', state='VOID')
        for amount, suffix, kind in [(2000, 'SALE', FinancialEntryKind.RECEITA_SORTEIO),
                                      (-2000, 'VOID', FinancialEntryKind.ESTORNO_SORTEIO)]:
            FinancialEntry.objects.create(event=self.event, raffle=raffle, amount_cents_signed=amount,
                kind=kind, source_reference=f'RAFFLE-{ticket.pk}-{suffix}', description='Bilhete anulado')
        unknown = FinancialEntry.objects.create(event=self.event, kind=FinancialEntryKind.DESPESA,
            amount_cents_signed=-300, source_reference='EXPENSE-referencia-legada', description='Origem antiga preservada')
        FinancialEntry.objects.create(event=self.other_event, kind=FinancialEntryKind.RECEITA_MANUAL,
            amount_cents_signed=70000, source_reference='externo', description='Outro evento')
        self.assertEqual([x.pk for x in self.rows()], [unknown.pk])
        self.assertEqual(financial_entries(self.event).count(), 3)
        self.assertEqual(get_financial_summary(self.event)['net_cents'], -300)

    def test_unknown_suffix_for_known_expense_is_not_silently_hidden(self):
        expense = self.create(status='PENDING', paid_at='')
        row = FinancialEntry.objects.create(event=self.event, kind=FinancialEntryKind.DESPESA,
            amount_cents_signed=-300, source_reference=f'EXPENSE-{expense.pk}-IMPORTADO', description='Importação antiga')
        self.assertEqual([x.pk for x in self.rows()], [row.pk])

    def test_current_manual_list_counts_scope_and_restore_redirect(self):
        row = self.new(description='Cadastro para restauração'); self.archive(row)
        url = reverse('gestao:movement_list', args=[self.event.pk])
        response = self.client.get(url)
        self.assertNotContains(response, row.description)
        self.assertEqual((response.context['active_count'], response.context['archived_count']), (0, 1))
        self.assertContains(self.client.get(url, {'archived': '1'}), row.description)
        row.refresh_from_db()
        response = self.client.post(reverse('gestao:movement_restore', args=[self.event.pk, row.pk]),
            {'revision': row.revision, 'reason': 'Restaurar cadastro conferido', 'confirmed': 'on'})
        self.assertRedirects(response, url)
        self.assertContains(self.client.get(url), row.description)
        self.assertNotContains(self.client.get(url, {'archived': '1'}), row.description)

    def test_history_marks_old_versions_and_removes_destructive_actions(self):
        row = self.new(); self.archive(row); self.archive(row, restore=True)
        entries = list(financial_entries(self.event).select_related('movement'))
        marked = mark_ledger_history(attach_entry_actions(entries, self.event), self.event)
        old = [entry for entry in marked if entry.ledger_history_only]
        self.assertEqual(len(old), 2)
        self.assertTrue(all(entry.remove_url == '' for entry in old))
        self.assertEqual(len([entry for entry in marked if not entry.ledger_history_only]), 1)
