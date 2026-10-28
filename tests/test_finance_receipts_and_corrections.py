import io,uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.events.models import MotorcycleRaffle
from apps.finance.forms import ExpenseForm,ExpenseEditForm,expense_version
from apps.finance.models import Expense,FinancialEntry,ExpenseStatus
from apps.finance.receipts import validate_receipt
from apps.finance.services import create_expense,settle_expense,attach_expense_receipt,update_expense,void_paid_expense
from apps.reporting.selectors import get_financial_summary
from tests import test_reporting_finance as fixtures


def png(name='comprovante.png'):
    buffer=io.BytesIO();Image.new('RGBA',(32,32),(250,90,10,150)).save(buffer,format='PNG')
    return SimpleUploadedFile(name,buffer.getvalue(),content_type='image/png')


class ExpenseReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.ReportingFinanceTests.setUpTestData.__func__(cls)
    expense_payload=fixtures.ReportingFinanceTests.expense_payload
    create=fixtures.ReportingFinanceTests.create

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(PRIVATE_MEDIA_ROOT=Path(self.temp.name)/'private')
        self.override.enable();self.addCleanup(self.override.disable)
        self.client.force_login(self.user);self.today=timezone.localdate()

    def form(self,**changes):
        upload=changes.pop('upload',None)
        form=ExpenseForm(self.expense_payload(**changes),{'proof_asset':upload} if upload else None,event=self.event)
        self.assertTrue(form.is_valid(),form.errors)
        return form

    def test_create_upload_private_and_idempotent_no_duplicate_outflow(self):
        payload=self.expense_payload();url=reverse('gestao:add_expense',args=[self.event.pk])
        self.assertEqual(self.client.post(url,{**payload,'proof_asset':png()}).status_code,302)
        self.assertEqual(self.client.post(url,{**payload,'proof_asset':png()}).status_code,302)
        expense=Expense.objects.get();self.assertTrue(expense.proof_asset)
        self.assertEqual(FinancialEntry.objects.count(),1)
        download=self.client.get(reverse('gestao:private_document_download',args=[self.event.pk,'despesa',expense.pk]))
        self.assertEqual(download.status_code,200);self.assertIn('attachment',download['Content-Disposition'])
        self.assertEqual(b''.join(download.streaming_content)[:8],b'\x89PNG\r\n\x1a\n')
        with self.assertRaises(ValueError):_=expense.proof_asset.url

    def test_pending_payment_with_receipt_and_attach_later(self):
        expense=self.create(status='PENDING')
        response=self.client.post(reverse('gestao:expense_pay',args=[self.event.pk,expense.pk]),{'paid_at':self.today.isoformat(),'proof_asset':png()})
        self.assertEqual(response.status_code,302)
        expense.refresh_from_db();self.assertEqual(expense.status,'PAID');self.assertTrue(expense.proof_asset)
        settle_expense(event=self.event,expense_id=expense.pk,actor=self.user,paid_at=self.today,proof=png())
        self.assertEqual(FinancialEntry.objects.count(),1)

    def test_attach_to_paid_never_posts_money_and_preserves_original(self):
        expense=self.create()
        attach_expense_receipt(event=self.event,expense_id=expense.pk,actor=self.user,proof=png())
        changed=png();changed.file.seek(0);buffer=io.BytesIO();Image.new('RGB',(35,35),'red').save(buffer,'PNG')
        changed=SimpleUploadedFile('novo.png',buffer.getvalue())
        with self.assertRaises(ValidationError):attach_expense_receipt(event=self.event,expense_id=expense.pk,actor=self.user,proof=changed)
        self.assertEqual(FinancialEntry.objects.count(),1)

    def test_bad_extension_mime_disguise_and_size_rejected(self):
        for upload in [SimpleUploadedFile('x.html',b'<script>x</script>'),SimpleUploadedFile('x.png',b'%PDF-1.7\n%%EOF'),
                       SimpleUploadedFile('x.pdf',b'not pdf')]:
            with self.subTest(name=upload.name),self.assertRaises(ValidationError):validate_receipt(upload)
        upload=png();upload.size=15*1024*1024+1
        with self.assertRaises(ValidationError):validate_receipt(upload)

    def test_audit_failure_rolls_back_database_and_written_receipt(self):
        form=self.form(upload=png())
        with patch('apps.finance.services.log_audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):create_expense(event=self.event,data=form.cleaned_data,actor=self.user)
        self.assertFalse(Expense.objects.exists());self.assertFalse(FinancialEntry.objects.exists())
        self.assertEqual(list(Path(self.temp.name).rglob('*.png')),[])

    def test_edit_paid_corrects_ledger_net_and_rejects_stale_draft(self):
        expense=self.create(amount='20,00')
        payload=self.expense_payload(amount='30,00',version=expense_version(expense),reason='Corrigido valor da nota fiscal')
        form=ExpenseEditForm(payload,expense=expense);self.assertTrue(form.is_valid(),form.errors)
        update_expense(event=self.event,expense_id=expense.pk,data=form.cleaned_data,actor=self.user)
        self.assertEqual(get_financial_summary(self.event)['paid_expenses_cents'],3000)
        self.assertEqual(FinancialEntry.objects.count(),3)
        with self.assertRaises(ValidationError):update_expense(event=self.event,expense_id=expense.pk,data=form.cleaned_data,actor=self.user)

    def test_void_paid_reverses_once_preserves_expense_and_receipt(self):
        form=self.form(upload=png(),amount='30,00')
        expense,_=create_expense(event=self.event,data=form.cleaned_data,actor=self.user)
        args=dict(event=self.event,expense_id=expense.pk,actor=self.user,version=expense_version(expense),reason='Duplicidade de lançamento detectada',confirmed=True)
        self.assertTrue(void_paid_expense(**args)[1]);self.assertFalse(void_paid_expense(**args)[1])
        expense.refresh_from_db();self.assertEqual(expense.status,'CANCELED');self.assertTrue(expense.proof_asset)
        self.assertEqual(get_financial_summary(self.event)['paid_expenses_cents'],0)
        self.assertEqual(FinancialEntry.objects.count(),2)

    def test_raffle_expense_tagged_once_and_finance_export_scope(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        expense,_=create_expense(event=self.event,data=self.form(amount='30,00',raffle_expense='on').cleaned_data,actor=self.user)
        entry=FinancialEntry.objects.get();self.assertEqual(entry.raffle,raffle)
        url=reverse('gestao:raffle_finance',args=[self.event.pk])
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['totals']['costs'],'30,00')
        self.assertEqual(response.context['totals']['net'],'-30,00')
        self.assertContains(self.client.get(url,{'export':'csv'}),'Alimentação da equipe')
        self.assertEqual(self.client.get(reverse('gestao:expense_edit',args=[self.other_event.pk,expense.pk])).status_code,403)
        self.assertEqual(self.client.get(url,{'date_start':'invalid'}).status_code,400)

    def test_move_paid_expense_between_general_and_raffle_keeps_global_net(self):
        raffle=MotorcycleRaffle.objects.create(event=self.event)
        expense=self.create(amount='20,00')
        payload=self.expense_payload(amount='20,00',version=expense_version(expense),reason='Vincular gasto à moto do sorteio',raffle_expense='on')
        form=ExpenseEditForm(payload,expense=expense);self.assertTrue(form.is_valid(),form.errors)
        update_expense(event=self.event,expense_id=expense.pk,data=form.cleaned_data,actor=self.user)
        self.assertEqual(get_financial_summary(self.event)['paid_expenses_cents'],2000)
        self.assertEqual(sum(FinancialEntry.objects.filter(raffle=raffle).values_list('amount_cents_signed',flat=True)),-2000)

    def test_forms_and_receipt_permissions(self):
        expense=self.create()
        for route in ['expense_edit','expense_pay','expense_proof','expense_void']:
            self.assertEqual(self.client.get(reverse('gestao:'+route,args=[self.event.pk,expense.pk])).status_code,200)
        self.user.event_memberships.all().update(role_code='REGISTRATION_AGENT')
        self.assertEqual(self.client.get(reverse('gestao:expense_proof',args=[self.event.pk,expense.pk])).status_code,403)
