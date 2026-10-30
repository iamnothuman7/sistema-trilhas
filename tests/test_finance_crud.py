import io
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core.models import AuditLog
from apps.finance.forms import expense_version, ExpenseForm
from apps.finance.models import ManualMovement, FinancialEntry, Vendor, Expense
from apps.finance.movements import save_movement, archive_movement
from apps.finance.views_crud import MovementForm
from apps.reporting.selectors import get_financial_summary
from tests import test_reporting_finance as fixtures


class FinanceCrudTests(TestCase):
    setUpTestData=classmethod(fixtures.ReportingFinanceTests.setUpTestData.__func__)
    create=fixtures.ReportingFinanceTests.create
    expense_payload=fixtures.ReportingFinanceTests.expense_payload

    def setUp(self):
        self.client.force_login(self.user);self.today=timezone.localdate()

    def payload(self,**kwargs):
        data=dict(kind='INCOME',description='Venda avulsa na sede',amount_cents='120,50',occurred_on=self.today.isoformat(),
            method='PIX',reference='Recebimento externo',notes='Conferido no banco',request_key=str(uuid.uuid4()))
        data.update(kwargs);return data

    def new(self,**kwargs):
        form=MovementForm(self.payload(**kwargs));self.assertTrue(form.is_valid(),form.errors)
        return save_movement(event=self.event,data=form.cleaned_data,actor=self.user)[0]

    def test_create_double_click_and_edit_reclassifies_period_without_rewriting_entries(self):
        data=self.payload();url=reverse('gestao:movement_create',args=[self.event.pk])
        for i in range(2):self.assertEqual(self.client.post(url,data).status_code,302)
        row=ManualMovement.objects.get();original=FinancialEntry.objects.get()
        self.assertEqual(get_financial_summary(self.event)['net_cents'],12050)
        yesterday=self.today-timedelta(days=1)
        edit=reverse('gestao:movement_edit',args=[self.event.pk,row.pk])
        changed=dict(data,amount_cents='95,75',occurred_on=yesterday.isoformat(),revision=row.revision,reason='Corrigir data e valor recebidos')
        self.assertEqual(self.client.post(edit,changed).status_code,302)
        self.assertEqual(self.client.post(edit,changed).status_code,400)
        original.refresh_from_db();self.assertEqual(original.amount_cents_signed,12050)
        self.assertEqual(FinancialEntry.objects.count(),3)
        self.assertEqual(get_financial_summary(self.event,self.today,self.today)['revenue_cents'],0)
        self.assertEqual(get_financial_summary(self.event,yesterday,yesterday)['revenue_cents'],9575)
        export=self.client.get(reverse('gestao:export_financial',args=[self.event.pk]))
        self.assertContains(export,'95,75')
        self.assertEqual(AuditLog.objects.filter(action='MANUAL_MOVEMENT_CREATED').count(),1)

    def test_remove_restore_repeated_cycles_exact_balance_and_stale_protection(self):
        row=self.new()
        for i in range(2):
            row.refresh_from_db();revision=row.revision
            for repeat in range(2):
                archive_movement(event=self.event,movement_id=row.pk,revision=revision,actor=self.user,reason='Lançamento incorreto')
            self.assertEqual(get_financial_summary(self.event)['net_cents'],0)
            row.refresh_from_db()
            archive_movement(event=self.event,movement_id=row.pk,revision=row.revision,actor=self.user,reason='Valor original conferido',restore=True)
            self.assertEqual(get_financial_summary(self.event)['net_cents'],12050)
            with self.assertRaises(ValidationError):
                archive_movement(event=self.event,movement_id=row.pk,revision=revision,actor=self.user,reason='Página antiga aberta')
        self.assertEqual(FinancialEntry.objects.count(),5)

    def test_transfer_does_not_change_profit_and_conversion_to_income_reconciles(self):
        row=self.new(kind='TRANSFER',amount_cents='200,00',reference='Mercado Pago para conta da equipe')
        summary=get_financial_summary(self.event)
        self.assertEqual(summary['net_cents'],0);self.assertEqual(summary['transfers_cents'],-20000)
        form=MovementForm(self.payload(kind='INCOME',revision=0,reason='Era um recebimento avulso'),movement=row)
        self.assertTrue(form.is_valid())
        save_movement(event=self.event,movement_id=row.pk,data=form.cleaned_data,actor=self.user)
        summary=get_financial_summary(self.event)
        self.assertEqual(summary['transfers_cents'],0);self.assertEqual(summary['net_cents'],12050)

    def test_invalid_future_amount_transfer_reference_and_reused_key_do_not_post(self):
        url=reverse('gestao:movement_create',args=[self.event.pk])
        for changes in [dict(amount_cents='0'),dict(amount_cents='1,234'),dict(amount_cents='1000000000,00'),
            dict(occurred_on=(self.today+timedelta(days=1)).isoformat()),dict(kind='TRANSFER',reference=''),dict(kind='FAKE')]:
            self.assertEqual(self.client.post(url,self.payload(**changes)).status_code,400)
        self.assertFalse(FinancialEntry.objects.exists())
        data=self.payload();self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,dict(data,amount_cents='99,00')).status_code,400)
        self.assertEqual(FinancialEntry.objects.count(),1)

    def test_no_mutation_on_get_confirmation_required_and_archived_form_rejected(self):
        row=self.new();remove=reverse('gestao:movement_remove',args=[self.event.pk,row.pk])
        self.assertEqual(self.client.get(remove).status_code,200)
        self.assertEqual(self.client.post(remove,dict(revision=0,reason='Remover lançamento errado')).status_code,400)
        self.assertEqual(self.client.post(remove,dict(revision=0,reason='Remover lançamento errado',confirmed='on')).status_code,302)
        row.refresh_from_db();self.assertIsNotNone(row.archived_at)
        edit=reverse('gestao:movement_edit',args=[self.event.pk,row.pk])
        self.assertEqual(self.client.post(edit,self.payload(revision=row.revision,reason='Não pode editar excluído')).status_code,400)
        self.assertNotContains(self.client.get(reverse('gestao:movement_list',args=[self.event.pk])),row.description)
        self.assertContains(self.client.get(reverse('gestao:movement_list',args=[self.event.pk]),{'archived':1}),row.description)

    def test_scope_permissions_private_proof_and_audit_failure_rollback(self):
        stream=io.BytesIO();Image.new('RGB',(5,5),'white').save(stream,format='PNG')
        with tempfile.TemporaryDirectory() as folder,override_settings(PRIVATE_MEDIA_ROOT=folder):
            form=MovementForm(self.payload(),{'proof_asset':SimpleUploadedFile('recibo.png',stream.getvalue(),content_type='image/png')})
            self.assertTrue(form.is_valid(),form.errors)
            row=save_movement(event=self.event,data=form.cleaned_data,actor=self.user)[0]
            doc=reverse('gestao:private_document_download',args=[self.event.pk,'movimentacao',row.pk])
            response=self.client.get(doc);self.assertEqual(response.status_code,200);response.close()
            self.assertEqual(self.client.get(reverse('gestao:movement_edit',args=[self.other_event.pk,row.pk])).status_code,403)
            self.membership.role_code='CONTENT';self.membership.save()
            self.assertEqual(self.client.get(doc).status_code,403)
            for route in ('movement_list','movement_create','vendor_list','vendor_create'):
                self.assertEqual(self.client.get(reverse('gestao:'+route,args=[self.event.pk])).status_code,403)
        with patch('apps.finance.movements.log_audit',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):self.new(description='Rollback example')
        self.assertEqual(ManualMovement.objects.count(),1);self.assertEqual(FinancialEntry.objects.count(),1)

    def test_vendor_create_edit_remove_restore_preserves_expense(self):
        url=reverse('gestao:vendor_create',args=[self.event.pk]);data=dict(name='Confecção Árvore',category='Camisetas',revision=0)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,dict(data,name='confeccao arvore')).status_code,400)
        row=Vendor.objects.get(name=data['name']);expense=self.create(vendor_id=row.pk)
        edit=reverse('gestao:vendor_edit',args=[self.event.pk,row.pk])
        data.update(name='Confecção da Serra',revision=row.revision)
        self.assertEqual(self.client.post(edit,data).status_code,302)
        self.assertEqual(self.client.post(edit,data).status_code,400)
        row.refresh_from_db()
        self.assertEqual(self.client.post(reverse('gestao:vendor_remove',args=[self.event.pk,row.pk]),
            dict(revision=row.revision,reason='Não trabalha mais com a equipe',confirmed='on')).status_code,302)
        expense.refresh_from_db();self.assertEqual(expense.vendor_id,row.pk)
        self.assertNotIn(row,ExpenseForm(event=self.event).fields['vendor_id'].queryset)
        row.refresh_from_db()
        self.assertEqual(self.client.post(reverse('gestao:vendor_restore',args=[self.event.pk,row.pk]),
            dict(revision=row.revision,reason='Retomamos a parceria',confirmed='on')).status_code,302)
        self.assertIn(row,ExpenseForm(event=self.event).fields['vendor_id'].queryset)

    def test_vendor_shared_scope_prevents_altering_other_event_supplier(self):
        from apps.events.models import Event
        Event.objects.create(organization=self.org,title='Evento sem acesso',slug='sem-acesso',starts_at=self.event.starts_at,
            ends_at=self.event.ends_at,registration_opens_at=self.event.registration_opens_at,registration_closes_at=self.event.registration_closes_at)
        self.assertEqual(self.client.get(reverse('gestao:vendor_list',args=[self.event.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('gestao:vendor_edit',args=[self.event.pk,self.vendor.pk])).status_code,403)

    def test_expense_paid_delete_restore_pending_then_pay_and_delete_again(self):
        expense=self.create(amount='150,00')
        for cycle in range(2):
            expense.refresh_from_db()
            self.assertEqual(self.client.post(reverse('gestao:expense_remove',args=[self.event.pk,expense.pk]),
                dict(version=expense_version(expense),reason='Saída cadastrada por engano',confirmed='on')).status_code,302)
            summary=get_financial_summary(self.event);self.assertEqual(summary['net_cents'],0)
            self.assertEqual(summary['expense_reconciliation_delta_cents'],0)
            expense.refresh_from_db()
            self.assertEqual(self.client.post(reverse('gestao:expense_restore',args=[self.event.pk,expense.pk]),
                dict(version=expense_version(expense),reason='Restaurar compromisso correto',confirmed='on')).status_code,302)
            expense.refresh_from_db();self.assertEqual(expense.status,'PENDING');self.assertIsNone(expense.paid_at)
            self.assertEqual(get_financial_summary(self.event)['pending_expenses_cents'],15000)
            self.assertEqual(self.client.post(reverse('gestao:expense_status',args=[self.event.pk,expense.pk]),
                dict(version=expense_version(expense),status='PAID',paid_at=self.today.isoformat())).status_code,302)
            self.assertEqual(get_financial_summary(self.event)['net_cents'],-15000)

    def test_finance_links_to_source_and_create_error_has_canonical_htmx_location(self):
        row=self.new();expense=self.create()
        page=self.client.get(reverse('gestao:finance',args=[self.event.pk]))
        self.assertContains(page,reverse('gestao:movement_edit',args=[self.event.pk,row.pk]))
        self.assertContains(page,reverse('gestao:expense_remove',args=[self.event.pk,expense.pk]))
        failed=self.client.post(reverse('gestao:add_expense',args=[self.event.pk]),self.expense_payload(amount='inválido'))
        self.assertEqual(failed.status_code,400)
        self.assertEqual(failed['HX-Replace-Url'],reverse('gestao:finance',args=[self.event.pk]))
