from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.finance.forms import expense_version
from apps.finance.models import Expense,FinancialEntry
from apps.reporting.selectors import get_financial_summary
from tests import test_reporting_finance as finance_fixtures


class ExpenseStatusTests(TestCase):
    setUpTestData=classmethod(finance_fixtures.ReportingFinanceTests.setUpTestData.__func__)
    expense_payload=finance_fixtures.ReportingFinanceTests.expense_payload
    create=finance_fixtures.ReportingFinanceTests.create

    def setUp(self):
        self.client.force_login(self.user)
        self.today=timezone.localdate()

    def test_reopen_repay_cycles_persist_and_ledger_never_duplicates(self):
        expense=self.create(amount='75,00')
        url=reverse('gestao:expense_status',args=[self.event.pk,expense.pk])
        for status in ['PENDING','PAID','PENDING','PAID']:
            expense.refresh_from_db()
            data={'status':status,'version':expense_version(expense),'paid_at':self.today.isoformat()}
            response=self.client.post(url,data)
            self.assertEqual(response.status_code,302)
            expense.refresh_from_db()
            self.assertEqual(expense.status,status)
            summary=get_financial_summary(self.event)
            self.assertEqual(summary['paid_expenses_cents'],7500 if status=='PAID' else 0)
            self.assertEqual(summary['pending_expenses_cents'],7500 if status=='PENDING' else 0)
            self.assertEqual(summary['expense_reconciliation_delta_cents'],0)
            count=FinancialEntry.objects.count()
            failed=self.client.post(url,data)
            self.assertEqual(failed.status_code,400)
            self.assertEqual(failed['HX-Replace-Url'],reverse('gestao:finance',args=[self.event.pk]))
            self.assertEqual(failed.context['management_workspace_url'],failed['HX-Replace-Url'])
            self.assertEqual(FinancialEntry.objects.count(),count)
            page=self.client.get(reverse('gestao:finance',args=[self.event.pk]),{'status':status})
            self.assertContains(page,expense.description)
        self.assertEqual(FinancialEntry.objects.count(),5)

    def test_scope_errors_and_return_filter(self):
        expense=self.create()
        url=reverse('gestao:expense_status',args=[self.event.pk,expense.pk])
        data={'status':'PENDING','version':expense_version(expense),'return_to':reverse('gestao:finance',args=[self.event.pk])+'?status=PAID'}
        self.assertEqual(self.client.post(url,data)['Location'],data['return_to'])
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(reverse('gestao:expense_status',args=[self.other_event.pk,expense.pk]),data).status_code,403)
        self.user.event_memberships.update(role_code='CONTENT')
        self.assertEqual(self.client.post(url,data).status_code,403)
