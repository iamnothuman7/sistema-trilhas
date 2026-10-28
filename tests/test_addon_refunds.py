"""Paid products must be returned explicitly before their base admission is canceled."""
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.exceptions import ValidationError
from django.test import TestCase,Client,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.events.models import Event,LegalDocumentVersion
from apps.finance.models import FinancialEntry
from apps.identity.models import User,UserPersonLink,EventMembership
from apps.payments.models import ChargeAttempt
from apps.payments.services import reconcile_payment,activate_registration_if_eligible
from apps.registrations.models import Entitlement,TermsAcceptance
from apps.registrations.management import cancel_paid_order,refundable_extra_review
from tests import test_payments as payment_fixtures
from tests import test_meeting_workflows as meeting_fixtures
from tests import test_finance_receipts_and_corrections as receipts
from tests import test_raffle_online_payments as raffle_fixtures


@override_settings(**payment_fixtures.MP_TEST_SETTINGS,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AddonRefundTests(TestCase):
    payment=payment_fixtures.PaymentReconciliationTests.payment
    paid_buyer=meeting_fixtures.MeetingWorkflowTests.paid_buyer
    addon_order=meeting_fixtures.MeetingWorkflowTests.addon_order

    def setUp(self):
        payment_fixtures.PaymentReconciliationTests.setUp(self)
        temporary=TemporaryDirectory();self.addCleanup(temporary.cleanup)
        storage=override_settings(MEDIA_ROOT=Path(temporary.name)/'public',PRIVATE_MEDIA_ROOT=Path(temporary.name)/'private')
        storage.enable();self.addCleanup(storage.disable)
        self.actor=User.objects.create_user(email='addon-refund-owner@example.test',is_staff=True)
        self.client.force_login(self.actor)

    def prepare(self,paid=True):
        self.buyer,self.item,key,self.extra_order=self.addon_order()
        UserPersonLink.objects.create(user=self.buyer,person=self.person)
        doc=LegalDocumentVersion.objects.create(event=self.event,kind='REGULAMENTO',version_number=1,content='Termos fictícios de teste')
        TermsAcceptance.objects.create(registration=self.registration,document_version=doc,accepting_user=self.buyer,evidence_hash='a'*64)
        activate_registration_if_eligible(self.registration);self.registration.refresh_from_db()
        self.raw_token=self.registration.credential.raw_token
        self.extra_charge=ChargeAttempt.objects.create(order=self.extra_order,idempotency_key=str(uuid.uuid4()),payment_method='pix',amount_cents=5000)
        self.extra_payment=self.payment(id=112233,transaction_amount=50,external_reference=self.extra_order.merchant_reference,
            metadata={'order_id':str(self.extra_order.pk),'charge_attempt_id':str(self.extra_charge.pk)})
        if paid:reconcile_payment(self.extra_payment)
        self.extra_order.refresh_from_db()

    def data(self,order,**changes):
        values=dict(channel='EXTERNAL',request_key=uuid.uuid4(),amount=order.total_cents,returned_on=timezone.localdate(),reference='DEVOLUCAO-FICTICIA',
            reason='Devolução já realizada de produtos de teste',confirmed=True)
        values.update(changes);return values

    def form_data(self,order,**changes):
        values=self.data(order)
        values['amount']=f'{values["amount"]/100:.2f}'
        values['returned_on']=values['returned_on'].isoformat()
        values.update(changes);return values

    def url(self):return reverse('gestao:addon_order_refund',args=[self.event.pk,self.extra_order.pk])

    def test_base_cancellation_blocks_paid_products_without_changing_receipts_or_benefits(self):
        self.prepare()
        with self.assertRaisesMessage(ValidationError,'compras extras pagas'):
            cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        self.order.refresh_from_db();self.extra_order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.order.status,'PAID');self.assertEqual(self.extra_order.status,'PAID')
        self.assertEqual(Entitlement.objects.get(source_order_line__order=self.extra_order).state,'AVAILABLE')
        self.assertTrue(self.registration.credential.is_valid)
        self.assertFalse(self.order.refunds.exists())

    def test_ui_extra_return_then_base_return_is_explicit_idempotent_and_preserves_qr_until_base_return(self):
        self.prepare()
        response=self.client.get(self.url())
        self.assertContains(response,'Somente os produtos extras deste pedido serão cancelados')
        self.assertContains(response,'Voltar ao financeiro')
        self.assertEqual(response.context['management_switch_module'],'finance')
        self.assertNotContains(response,'Todas as inscrições abaixo serão afetadas')
        data=self.form_data(self.extra_order,proof_asset=receipts.png())
        result=self.client.post(self.url(),data)
        self.assertRedirects(result,reverse('gestao:finance',args=[self.event.pk]),fetch_redirect_response=False)
        self.extra_order.refresh_from_db();self.order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.extra_order.status,'REFUNDED');self.assertEqual(self.order.status,'PAID')
        self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertEqual(self.registration.credential.raw_token,self.raw_token);self.assertTrue(self.registration.credential.is_valid)
        self.assertEqual(Entitlement.objects.get(source_order_line__order=self.extra_order).state,'REVOKED')
        self.assertFalse(self.registration.entitlements.exclude(benefit_type='ADICIONAL').filter(state='REVOKED').exists())
        refund=self.extra_order.refunds.get();self.assertTrue(refund.proof_asset)
        data.pop('proof_asset');self.assertEqual(self.client.post(self.url(),data).status_code,302)
        self.assertEqual(FinancialEntry.objects.filter(order=self.extra_order,kind='REEMBOLSO').count(),1)
        self.http.assert_not_called()
        base=cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'CANCELED')
        self.assertFalse(self.registration.credential.is_valid)

    def test_partial_product_return_still_blocks_base_until_remaining_balance_returned(self):
        self.prepare()
        reconcile_payment({**self.extra_payment,'transaction_amount_refunded':10,'date_last_updated':(self.now+timedelta(seconds=1)).isoformat()})
        with self.assertRaises(ValidationError):
            cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        page=self.client.get(self.url());self.assertEqual(page.context['refund_amount'],4000)
        response=self.client.post(self.url(),self.form_data(self.extra_order,amount='40,00'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(sum(self.extra_order.refunds.values_list('amount_cents',flat=True)),5000)
        cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)

    def test_finance_and_registration_show_action_and_keep_event_scope(self):
        self.prepare()
        finance=self.client.get(reverse('gestao:finance',args=[self.event.pk]));self.assertContains(finance,self.url())
        detail=self.client.get(reverse('gestao:registration_detail',args=[self.event.pk,self.registration.pk]));self.assertContains(detail,self.url())
        another=Event.objects.create(organization=self.event.organization,title='Outro evento',slug='outro-addon-refund',
            starts_at=self.event.starts_at,ends_at=self.event.ends_at,registration_opens_at=self.event.registration_opens_at,registration_closes_at=self.event.registration_closes_at)
        self.assertEqual(self.client.get(reverse('gestao:addon_order_refund',args=[another.pk,self.extra_order.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('gestao:addon_order_refund',args=[self.event.pk,self.order.pk])).status_code,404)
        user=User.objects.create_user(email='addon-finance@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='FINANCE');self.client.force_login(user)
        self.assertEqual(self.client.get(self.url()).status_code,200)
        self.assertContains(self.client.get(reverse('gestao:finance',args=[self.event.pk])),self.url())
        self.assertEqual(self.client.get(reverse('gestao:addon_order_refund',args=[another.pk,self.extra_order.pk])).status_code,403)

    def test_registration_only_role_and_missing_csrf_cannot_return_products(self):
        self.prepare()
        user=User.objects.create_user(email='addon-registration@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='REGISTRATION_AGENT');self.client.force_login(user)
        self.assertEqual(self.client.post(self.url(),self.form_data(self.extra_order)).status_code,403)
        detail=self.client.get(reverse('gestao:registration_detail',args=[self.event.pk,self.registration.pk]))
        self.assertNotContains(detail,self.url())
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.actor)
        self.assertEqual(strict.post(self.url(),self.form_data(self.extra_order)).status_code,403)
        self.assertFalse(self.extra_order.refunds.exists())

    def test_inexact_amount_or_missing_confirmation_never_changes_finance(self):
        self.prepare()
        for changes in [dict(amount='49,00'),dict(confirmed='')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.url(),self.form_data(self.extra_order,**changes)).status_code,400)
        self.extra_order.refresh_from_db();self.assertEqual(self.extra_order.status,'PAID')
        self.assertFalse(self.extra_order.refunds.exists())
        self.assertFalse(FinancialEntry.objects.filter(kind='REEMBOLSO').exists())

    def test_approval_after_base_cancellation_records_money_for_review_without_granting_products(self):
        self.prepare(paid=False)
        cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        reconcile_payment(self.extra_payment)
        self.extra_order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.extra_order.status,'PAID_NEEDS_REVIEW')
        self.assertEqual(self.registration.commercial_state,'CANCELED')
        self.assertFalse(Entitlement.objects.filter(source_order_line__order=self.extra_order).exists())
        self.assertEqual(FinancialEntry.objects.filter(order=self.extra_order,kind='RECEITA_ADICIONAL').count(),1)

    def test_single_late_extra_receipt_can_be_returned_and_replay_keeps_it_refunded(self):
        self.prepare(paid=False)
        cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        reconcile_payment(self.extra_payment);self.extra_order.refresh_from_db()
        self.assertTrue(refundable_extra_review(self.extra_order))
        page=self.client.get(self.url());self.assertContains(page,'Há um único pagamento integral confirmado')
        self.assertContains(page,'Confirmar cancelamento e devolução registrada')
        result=self.client.post(self.url(),self.form_data(self.extra_order))
        self.assertEqual(result.status_code,302)
        reconcile_payment({**self.extra_payment,'date_last_updated':(self.now+timedelta(minutes=1)).isoformat()})
        self.extra_order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.extra_order.status,'REFUNDED')
        self.assertEqual(self.registration.commercial_state,'CANCELED')
        self.assertFalse(self.registration.credential.is_valid)
        self.assertEqual(FinancialEntry.objects.filter(order=self.extra_order,kind='REEMBOLSO').count(),1)

    def test_ambiguous_extra_receipts_have_explanation_without_refund_form(self):
        self.prepare(paid=False)
        cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.data(self.order),actor=self.actor)
        reconcile_payment(self.extra_payment)
        second=ChargeAttempt.objects.create(order=self.extra_order,idempotency_key=str(uuid.uuid4()),payment_method='pix',amount_cents=5000)
        reconcile_payment({**self.extra_payment,'id':112234,'metadata':{'order_id':str(self.extra_order.pk),'charge_attempt_id':str(second.pk)}})
        self.extra_order.refresh_from_db();self.assertFalse(refundable_extra_review(self.extra_order))
        page=self.client.get(self.url())
        self.assertContains(page,'registro de devolução está bloqueado')
        self.assertContains(page,'Conferir transações no Mercado Pago')
        self.assertNotContains(page,'Confirmar cancelamento e devolução registrada')
        self.assertEqual(self.client.post(self.url(),self.form_data(self.extra_order)).status_code,400)
        self.assertFalse(self.extra_order.refunds.exists())

    def test_new_payment_after_manual_extra_return_remains_a_review_case(self):
        self.prepare()
        self.assertEqual(self.client.post(self.url(),self.form_data(self.extra_order)).status_code,302)
        another=ChargeAttempt.objects.create(order=self.extra_order,idempotency_key=str(uuid.uuid4()),payment_method='pix',amount_cents=5000)
        reconcile_payment({**self.extra_payment,'id':112234,'metadata':{'order_id':str(self.extra_order.pk),'charge_attempt_id':str(another.pk)}})
        self.extra_order.refresh_from_db()
        self.assertEqual(self.extra_order.status,'PAID_NEEDS_REVIEW')
        self.assertFalse(refundable_extra_review(self.extra_order))
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertTrue(self.registration.credential.is_valid)


@override_settings(**payment_fixtures.MP_TEST_SETTINGS)
class RaffleReviewRefundTests(TestCase):
    setUp=payment_fixtures.PaymentReconciliationTests.setUp
    payment=payment_fixtures.PaymentReconciliationTests.payment
    prepare=raffle_fixtures.OnlineRaffleTests.prepare
    reserve=raffle_fixtures.OnlineRaffleTests.reserve
    approve=raffle_fixtures.OnlineRaffleTests.approve

    def test_late_raffle_receipt_can_be_returned_without_issuing_numbers_or_reopening_base(self):
        self.prepare();extra=self.reserve()
        cancel_paid_order(event=self.event,order_id=self.order.pk,actor=self.user,data=dict(channel='EXTERNAL',request_key=uuid.uuid4(),
            amount=self.order.total_cents,returned_on=timezone.localdate(),reference='BASE-DEVOLVIDA',reason='Devolução integral fictícia da inscrição base',confirmed=True))
        charge,payment=self.approve(extra)
        self.assertEqual(extra.status,'PAID_NEEDS_REVIEW');self.assertTrue(refundable_extra_review(extra))
        url=reverse('gestao:raffle_order_refund',args=[self.event.pk,extra.pk])
        self.assertContains(self.client.get(url),'Há um único pagamento integral confirmado')
        response=self.client.post(url,dict(channel='EXTERNAL',request_key=uuid.uuid4(),amount='15,00',returned_on=timezone.localdate().isoformat(),
            reference='EXTRA-DEVOLVIDO',reason='Devolução fictícia integral dos números extras',confirmed='on'))
        self.assertRedirects(response,reverse('gestao:raffle_finance',args=[self.event.pk]),fetch_redirect_response=False)
        reconcile_payment(payment);extra.refresh_from_db();self.order.refresh_from_db()
        self.assertEqual(extra.status,'REFUNDED');self.assertEqual(self.order.status,'REFUNDED')
        self.assertFalse(extra.lines.get().raffle_tickets.exists())
        self.assertEqual(FinancialEntry.objects.filter(order=extra,kind='ESTORNO_SORTEIO').count(),1)
        self.http.assert_not_called()
