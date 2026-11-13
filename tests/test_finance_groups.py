from datetime import timedelta
from html import unescape

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.finance.forms import expense_version
from apps.finance.models import Expense, FinancialEntry
from apps.finance.services import cancel_pending_expense
from tests import test_reporting_finance as finance_fixtures


class FinanceGroupTests(TestCase):
    setUpTestData = classmethod(finance_fixtures.ReportingFinanceTests.setUpTestData.__func__)
    expense_payload = finance_fixtures.ReportingFinanceTests.expense_payload
    create = finance_fixtures.ReportingFinanceTests.create

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.url = reverse('gestao:finance', args=[self.event.pk])

    def test_overview_groups_active_accounts_without_showing_rows(self):
        pending = self.create(description='Conta ainda aberta', status='PENDING', amount='150,00')
        paid = self.create(description='Conta já quitada', amount='80,00')
        removed = self.create(description='Conta cancelada', status='PENDING', amount='45,00')
        cancel_pending_expense(event=self.event, expense_id=removed.pk, actor=self.user, reason='Compromisso de teste cancelado')
        Expense.objects.create(event=self.other_event, description='Conta fora do acesso', amount_cents=999999, due_at=self.today)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['finance_section'], 'overview')
        self.assertEqual(response.context['expense_groups'], {
            'PENDING': {'count': 1, 'total': 15000}, 'PAID': {'count': 1, 'total': 8000}, 'CANCELED': {'count': 1, 'total': 4500},
        })
        self.assertNotContains(response, 'data-expense=')
        self.assertNotContains(response, 'Conta fora do acesso')
        for status, item in [('PENDING', pending), ('PAID', paid), ('CANCELED', removed)]:
            page = self.client.get(self.url, {'status': status})
            self.assertEqual(list(page.context['expenses']), [item])
            self.assertContains(page, f'data-expense="{item.pk}"')
        active = self.client.get(self.url, {'status': 'ALL'})
        self.assertEqual({row.pk for row in active.context['expenses']}, {pending.pk, paid.pk})
        self.assertNotContains(active, 'Conta cancelada')

    def test_period_uses_payment_date_for_paid_accounts_and_due_date_for_pending(self):
        paid = self.create(description='Paga hoje com vencimento futuro', amount='75,00', due_at=(self.today + timedelta(days=20)).isoformat())
        self.create(description='Vence depois', status='PENDING', amount='22,00', due_at=(self.today + timedelta(days=20)).isoformat())
        response = self.client.get(self.url, {'status': 'PAID', 'date_start': self.today.isoformat(), 'date_end': self.today.isoformat()})
        self.assertEqual(list(response.context['expenses']), [paid])
        self.assertEqual(response.context['expense_groups']['PAID'], {'count': 1, 'total': 7500})
        self.assertEqual(response.context['expense_groups']['PENDING']['count'], 0)
        self.assertEqual(response.context['finance']['paid_expenses_cents'], 7500)
        self.assertIn('status=PAID', response.context['filter_query'])

    def test_status_change_refreshes_groups_and_preserves_selected_return(self):
        expense = self.create(status='PENDING', amount='110,00')
        response = self.client.post(reverse('gestao:expense_status', args=[self.event.pk, expense.pk]), {
            'version': expense_version(expense), 'status': 'PAID', 'paid_at': self.today.isoformat(),
            'return_to': self.url + '?status=PENDING',
        })
        self.assertRedirects(response, self.url + '?status=PENDING')
        page = self.client.get(self.url)
        self.assertEqual(page.context['expense_groups']['PENDING']['count'], 0)
        self.assertEqual(page.context['expense_groups']['PAID']['total'], 11000)
        self.assertEqual(page.context['finance']['net_cents'], -11000)

    def test_failed_status_edit_remains_visible_even_from_later_page(self):
        for number in range(22):
            self.create(description=f'Conta {number}', status='PENDING', amount='10,00')
        expense = Expense.objects.filter(event=self.event).order_by('pk').first()
        response = self.client.post(reverse('gestao:expense_status', args=[self.event.pk, expense.pk]), {
            'version': 'stale', 'status': 'PAID', 'paid_at': self.today.isoformat(),
            'return_to': self.url + '?status=PENDING&expenses_page=2',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['finance_section'], 'expenses')
        self.assertContains(response, f'id="status-{expense.pk}"', status_code=400)
        expense.refresh_from_db()
        self.assertEqual(expense.status, 'PENDING')
        self.assertFalse(FinancialEntry.objects.exists())

    def test_pagination_and_history_selection_keep_filters(self):
        for number in range(21):
            self.create(description=f'Pendente {number}', status='PENDING')
        response = self.client.get(self.url, {'status': 'PENDING', 'expenses_page': 2})
        self.assertEqual(response.context['expenses_page'].number, 2)
        self.assertEqual(response.context['expense_groups']['PENDING']['count'], 21)
        self.assertIn('status=PENDING&expenses_page=1', unescape(response.content.decode()))
        history = self.client.get(self.url, {'panel': 'ledger', 'ledger_mode': 'history'})
        self.assertTrue(history.context['ledger_history'])
        self.assertEqual(history.context['finance_section'], 'ledger')
        self.assertIn('ledger_mode=history', history.context['filter_query'])

    def test_invalid_period_does_not_expose_unfiltered_groups(self):
        self.create(amount='33,00')
        response = self.client.get(self.url, {'date_start': 'invalid'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['expense_groups']['PAID']['count'], 0)
        self.assertEqual(response.context['current_entry_count'], 0)
        self.assertEqual(self.client.get(reverse('gestao:finance', args=[self.other_event.pk])).status_code, 403)
