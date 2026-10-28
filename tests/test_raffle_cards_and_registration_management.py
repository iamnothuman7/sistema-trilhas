import io,uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.events.models import MotorcycleRaffle,LegalDocumentVersion
from apps.events.models_raffles import RaffleTicket
from apps.finance.models import FinancialEntry
from apps.identity.models import User,UserPersonLink,EventMembership
from apps.payments.models import Refund
from apps.payments.services import reconcile_payment,activate_registration_if_eligible
from apps.registrations.models import Registration,TermsAcceptance,Entitlement
from apps.registrations.forms_management import RegistrationEditForm
from apps.registrations.management import edit_registration,registration_version,cancel_paid_order
from tests import test_payments as fixtures
from tests.test_finance_receipts_and_corrections import png


@override_settings(**fixtures.MP_TEST_SETTINGS)
class RaffleCardAndManagementTests(TestCase):
    setUp_payment=fixtures.PaymentReconciliationTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment

    def setUp(self):
        self.setUp_payment()
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(MEDIA_ROOT=Path(self.temp.name)/'public',PRIVATE_MEDIA_ROOT=Path(self.temp.name)/'private')
        self.override.enable();self.addCleanup(self.override.disable)
        self.owner=User.objects.create_user(email='owner-test@example.test',full_name='Gestor',is_staff=True)
        self.client.force_login(self.owner)
        self.raffle=MotorcycleRaffle.objects.create(event=self.event,is_public=True,motorcycle_count=2)
        self.event.status='PUBLISHED';self.event.save()

    def pay(self):
        reconcile_payment(self.payment());self.order.refresh_from_db();self.registration.refresh_from_db()

    def ready(self):
        self.pay()
        self.holder=User.objects.create_user(email='holder@example.test',full_name='Piloto')
        UserPersonLink.objects.create(user=self.holder,person=self.person)
        doc=LegalDocumentVersion.objects.create(event=self.event,kind='REGULAMENTO',version_number=1,content='Regras pessoais de teste')
        TermsAcceptance.objects.create(registration=self.registration,document_version=doc,accepting_user=self.holder,evidence_hash='a'*64)
        activate_registration_if_eligible(self.registration);self.registration.refresh_from_db()

    def refund_data(self,**changes):
        data=dict(request_key=uuid.uuid4(),amount=18001,returned_on=timezone.localdate(),reference='PIX-DEVOLUCAO-TESTE',
            reason='Devolução realizada a pedido do participante',confirmed=True,proof_asset=None)
        data.update(changes);return data

    def refund(self,data):
        return cancel_paid_order(event=self.event,order_id=self.order.pk,data=data,actor=self.owner)

    def test_approved_payment_confirms_without_organization_and_one_included_number(self):
        self.pay();reconcile_payment(self.payment())
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertEqual(self.registration.operational_state,'NOT_READY')
        self.assertEqual(RaffleTicket.objects.filter(registration=self.registration).count(),1)
        self.assertFalse(FinancialEntry.objects.filter(kind='RECEITA_SORTEIO').exists())

    def test_png_download_requires_ready_registration_and_scoped_owner(self):
        self.pay();self.client.force_login(self.order.purchaser_user)
        url=reverse('minha_conta:credential_image',args=[self.registration.pk])
        self.assertEqual(self.client.get(url).status_code,403)
        self.ready();self.client.force_login(self.holder)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200);self.assertEqual(response['Content-Type'],'image/png')
        self.assertIn('no-store',response['Cache-Control'])
        with Image.open(io.BytesIO(response.content)) as image:self.assertEqual(image.size,(1080,1540))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code,404)

    def test_refund_revokes_qr_entitlements_number_and_posts_once(self):
        self.ready();data=self.refund_data(proof_asset=png())
        refund,created=self.refund(data);self.assertTrue(created)
        self.assertFalse(self.refund(data)[1])
        self.registration.refresh_from_db();self.order.refresh_from_db();self.capacity.refresh_from_db()
        self.assertEqual(self.order.status,'REFUNDED');self.assertIsNotNone(self.registration.archived_at)
        self.assertEqual(self.registration.commercial_state,'CANCELED');self.assertFalse(self.registration.credential.is_valid)
        self.assertEqual(self.registration.raffle_ticket.state,'VOID')
        self.assertFalse(Entitlement.objects.filter(registration=self.registration).exclude(state='REVOKED').exists())
        self.assertEqual(self.capacity.committed_count,0)
        self.assertEqual(FinancialEntry.objects.filter(kind='REEMBOLSO').count(),1)
        self.assertTrue(refund.proof_asset)
        response=self.client.get(reverse('gestao:private_document_download',args=[self.event.pk,'reembolso',refund.pk]))
        self.assertEqual(response.status_code,200);response.close()

    def test_later_provider_approved_never_reopens_manually_refunded_order(self):
        self.pay();self.refund(self.refund_data())
        reconcile_payment(self.payment(date_last_updated=(self.now+timedelta(minutes=5)).isoformat()))
        self.registration.refresh_from_db();self.order.refresh_from_db()
        self.assertEqual(self.order.status,'REFUNDED');self.assertIsNotNone(self.registration.archived_at)
        self.assertEqual(self.registration.raffle_ticket.state,'VOID')

    def test_legacy_status_endpoint_cannot_skip_financial_refund(self):
        self.pay()
        response=self.client.post(reverse('gestao:registration_change_status',args=[self.event.pk,self.registration.pk]),
            {'commercial_state':'CANCELED','reason':'Não deve cancelar sem devolução'})
        self.assertEqual(response.status_code,302)
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertFalse(Refund.objects.exists())

    def test_refund_requires_exact_total_actual_confirmation_and_reference(self):
        self.pay()
        for changes in [dict(amount=12000),dict(confirmed=False),dict(reference=''),dict(returned_on=timezone.localdate()+timedelta(days=1))]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):self.refund(self.refund_data(**changes))
        self.assertFalse(Refund.objects.exists())

    def test_refund_audit_failure_compensates_private_file_and_entire_mutation(self):
        self.pay()
        with patch('apps.registrations.management.log_audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):self.refund(self.refund_data(proof_asset=png()))
        self.order.refresh_from_db();self.assertEqual(self.order.status,'PAID')
        self.assertFalse(Refund.objects.exists());self.assertEqual(list(Path(self.temp.name).rglob('*.png')),[])

    def test_group_cancellation_is_explicit_and_affects_entire_order(self):
        self.pay()
        from apps.checkout.models import OrderLine
        line=OrderLine.objects.create(order=self.order,product=self.product,intended_person_name='Segundo participante')
        second=Registration.objects.create(event=self.event,person=self.person,source_order_line=line,commercial_state='ACTIVE')
        response=self.client.get(reverse('gestao:registration_refund',args=[self.event.pk,self.registration.pk]))
        self.assertContains(response,'Todas as inscrições abaixo serão afetadas')
        self.assertEqual(len(response.context['affected_regs']),2)
        self.refund(self.refund_data());second.refresh_from_db();self.assertEqual(second.commercial_state,'CANCELED')

    def test_edit_keeps_cpf_payment_and_current_qr(self):
        self.ready();old_token=self.registration.credential.raw_token
        form=RegistrationEditForm(dict(version=registration_version(self.registration),full_name='Nome corrigido',email='contato@example.test',phone='88999998888',
            city='Ubajara',state='ce',team_name='Morcegos',vehicle_type='UTV',shirt_size=self.registration.shirt_size,reason='Correção solicitada pelo participante'),registration=self.registration)
        self.assertTrue(form.is_valid(),form.errors)
        edit_registration(event=self.event,reg_id=self.registration.pk,data=form.cleaned_data,actor=self.owner)
        self.registration.refresh_from_db();self.assertEqual(self.registration.person.full_name,'Nome corrigido')
        self.assertEqual(self.registration.credential.raw_token,old_token)
        self.assertEqual(self.registration.source_order_line.order.total_cents,18001)
        with self.assertRaises(ValidationError):edit_registration(event=self.event,reg_id=self.registration.pk,data=form.cleaned_data,actor=self.owner)

    def test_shared_person_correction_does_not_change_other_registration(self):
        self.pay()
        from apps.checkout.models import OrderLine
        line=OrderLine.objects.create(order=self.order,product=self.product,intended_person_name='Outro cadastro')
        other=Registration.objects.create(event=self.event,person=self.person,source_order_line=line,commercial_state='CANCELED')
        form=RegistrationEditForm(dict(version=registration_version(self.registration),full_name='Corrigido apenas aqui',email='',phone='',city='Tianguá',state='CE',
            team_name='',vehicle_type='',shirt_size=self.registration.shirt_size,reason='Correção de cadastro deste evento'),registration=self.registration)
        self.assertTrue(form.is_valid(),form.errors)
        edit_registration(event=self.event,reg_id=self.registration.pk,data=form.cleaned_data,actor=self.owner)
        self.registration.refresh_from_db();other.refresh_from_db()
        self.assertNotEqual(self.registration.person_id,other.person_id);self.assertEqual(other.person.full_name,'Participante Teste')

    def raffle_payload(self):
        return dict(title='Duas motos no Trilhão',motorcycle_count='2',paper_start='2000',paper_end='3000',registration_tickets_enabled='on',
                    is_public='on',image_style='CUTOUT',motorcycle_model='Motos de trilha')

    def test_upload_transparent_art_sanitized_and_public_layout(self):
        url=reverse('gestao:raffle_management',args=[self.event.pk])
        response=self.client.post(url,{**self.raffle_payload(),'primary_image':png()})
        self.assertEqual(response.status_code,302)
        self.raffle.refresh_from_db()
        with self.raffle.primary_image.open('rb') as asset:
            with Image.open(asset) as image:
                self.assertEqual(image.format,'WEBP');self.assertIn('A',image.getbands())
        public=self.client.get(reverse('events:raffle',args=[self.event.slug]))
        self.assertContains(public,self.raffle.primary_image.url)
        self.assertContains(public,'Sua inscrição já inclui um número')
        self.assertFalse(self.raffle.purchase_available)

    def test_invalid_image_and_audit_rollback(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        url=reverse('gestao:raffle_management',args=[self.event.pk])
        self.assertEqual(self.client.post(url,{**self.raffle_payload(),'primary_image':SimpleUploadedFile('moto.svg',b'<svg></svg>')}).status_code,400)
        with patch('apps.events.views_raffles.log_audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):self.client.post(url,{**self.raffle_payload(),'primary_image':png()})
        self.raffle.refresh_from_db();self.assertFalse(self.raffle.primary_image)
        self.assertEqual(list(Path(self.temp.name).rglob('*.webp')),[])

    def test_finance_role_cannot_cancel_without_permission_or_cross_event(self):
        self.pay();self.owner.is_staff=False;self.owner.save()
        EventMembership.objects.create(user=self.owner,event=self.event,role_code='REGISTRATION_AGENT')
        self.assertEqual(self.client.get(reverse('gestao:registration_refund',args=[self.event.pk,self.registration.pk])).status_code,403)
