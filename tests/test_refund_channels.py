import uuid
from datetime import timedelta
from unittest.mock import patch
import requests
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase,TransactionTestCase,Client,override_settings
from django.urls import reverse
from apps.checkout.models import Order
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.finance.models import FinancialEntry
from apps.payments.models import Refund,RefundChannel,RefundState,PaymentTransaction
from apps.payments.services import reconcile_payment,_commit_capacity
from apps.registrations.management import cancel_paid_order
from tests import test_payments as fixtures
from tests import test_raffle_cards_and_registration_management as management


@override_settings(**fixtures.MP_TEST_SETTINGS,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RefundChannelTests(TestCase):
    setUp_payment=fixtures.PaymentReconciliationTests.setUp
    setUp=management.RaffleCardAndManagementTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment
    pay=management.RaffleCardAndManagementTests.pay
    ready=management.RaffleCardAndManagementTests.ready
    refund_data=management.RaffleCardAndManagementTests.refund_data
    webhook=fixtures.PaymentReconciliationTests.webhook

    def url(self):return reverse('gestao:registration_refund',args=[self.event.pk,self.registration.pk])

    def post_data(self,channel='EXTERNAL'):
        values=self.refund_data(channel=channel);values['amount']='180,01';values['returned_on']=values['returned_on'].isoformat()
        values.pop('proof_asset',None)
        return values

    def refund_sum(self):return sum(FinancialEntry.objects.filter(order=self.order,kind='REEMBOLSO').values_list('amount_cents_signed',flat=True))

    def provider_return(self,amount=180.01):
        return self.payment(status='refunded' if amount==180.01 else 'approved',transaction_amount_refunded=amount,
            date_last_updated=(self.now+timedelta(minutes=1)).isoformat())

    def mock_provider(self,payment=None):
        self.http.side_effect=None;self.http.return_value=fixtures.response(payment or self.provider_return())

    def legacy(self):
        self.ready()
        refund,_=cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.refund_data(channel='EXTERNAL'),actor=self.owner)
        Refund.objects.filter(pk=refund.pk).update(channel=RefundChannel.UNKNOWN)
        refund.refresh_from_db();return refund

    def review_url(self,refund):return reverse('gestao:refund_channel_review',args=[self.event.pk,refund.pk])

    def classification(self,channel):return {'channel':channel,'reason':'Conferência fictícia dos comprovantes do teste','confirmed':'on'}

    def test_new_manual_returns_require_explicit_external_channel(self):
        self.pay();data=self.post_data();data.pop('channel')
        self.assertEqual(self.client.post(self.url(),data).status_code,400)
        with self.assertRaises(ValidationError):
            cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.refund_data(channel=''),actor=self.owner)
        with self.assertRaises(ValidationError):
            cancel_paid_order(event=self.event,order_id=self.order.pk,data=self.refund_data(channel='MERCADO_PAGO'),actor=self.owner)
        self.assertFalse(Refund.objects.exists());self.http.assert_not_called()

    def test_external_return_and_later_separate_provider_return_are_both_posted(self):
        self.pay()
        self.assertEqual(self.client.post(self.url(),self.post_data()).status_code,302)
        self.assertEqual(Refund.objects.get().channel,RefundChannel.EXTERNAL)
        reconcile_payment(self.provider_return())
        self.assertEqual(self.refund_sum(),-36002)
        self.assertEqual(set(Refund.objects.values_list('channel',flat=True)),{'EXTERNAL','MERCADO_PAGO'})
        reconcile_payment(self.provider_return());self.assertEqual(self.refund_sum(),-36002)

    def test_partial_provider_return_does_not_reopen_an_order_fully_returned_externally(self):
        self.ready();self.assertEqual(self.client.post(self.url(),self.post_data()).status_code,302)
        reconcile_payment(self.provider_return(90))
        self.order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.order.status,'REFUNDED')
        self.assertEqual(self.registration.commercial_state,'CANCELED');self.assertFalse(self.registration.credential.is_valid)
        self.assertEqual(self.refund_sum(),-27001)
        due=self.order.receivables.get();self.assertEqual((due.status,due.allocated_paid_cents),('CANCELED',0))

    def test_mp_channel_reads_existing_payment_and_never_creates_manual_refund(self):
        self.ready();self.mock_provider()
        data=self.post_data('MERCADO_PAGO')
        self.assertEqual(self.client.post(self.url(),data).status_code,302)
        self.assertEqual(self.refund_sum(),-18001)
        self.assertEqual(Refund.objects.count(),1)
        refund=Refund.objects.get();self.assertEqual(refund.channel,'MERCADO_PAGO');self.assertEqual(refund.state,'SUCCEEDED')
        self.assertEqual(self.client.post(self.url(),data).status_code,302)
        self.assertEqual(self.refund_sum(),-18001);self.assertEqual(Refund.objects.count(),1)
        self.assertTrue(all(call.args[0]=='GET' for call in self.http.call_args_list))
        self.registration.refresh_from_db();self.assertFalse(self.registration.credential.is_valid)

    def test_unconfirmed_or_wrong_account_mp_response_cannot_invent_a_return(self):
        self.pay()
        for payment in [self.payment(),self.provider_return()|{'collector_id':9999}]:
            self.mock_provider(payment)
            self.assertEqual(self.client.post(self.url(),self.post_data('MERCADO_PAGO')).status_code,400)
            self.assertFalse(Refund.objects.exists());self.assertEqual(self.refund_sum(),0)
        self.http.side_effect=requests.Timeout()
        self.assertEqual(self.client.post(self.url(),self.post_data('MERCADO_PAGO')).status_code,400)
        self.assertFalse(Refund.objects.exists())

    def test_legacy_unknown_webhook_blocks_additional_money_and_shows_review(self):
        refund=self.legacy();result=self.webhook(self.provider_return())
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.refund_sum(),-18001);self.assertEqual(Refund.objects.count(),1)
        self.order.refresh_from_db();self.charge.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.order.status,'PAID_NEEDS_REVIEW');self.assertEqual(self.charge.error_code,'REFUND_CHANNEL_REVIEW')
        self.assertFalse(self.registration.credential.is_valid)
        self.assertEqual(PaymentTransaction.objects.get().posted_refund_cents,0)
        page=self.client.get(reverse('gestao:finance',args=[self.event.pk]))
        self.assertContains(page,self.review_url(refund));self.assertContains(page,'lançamento adicional aguarda conferência')
        self.assertEqual(self.webhook(self.provider_return()).status_code,200);self.assertEqual(self.refund_sum(),-18001)

    def test_legacy_external_classification_allows_real_second_return_and_retries_are_idempotent(self):
        refund=self.legacy();reconcile_payment(self.provider_return());self.mock_provider()
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('EXTERNAL')).status_code,302)
        self.assertEqual(self.refund_sum(),-36002);self.assertEqual(Refund.objects.count(),2)
        self.charge.refresh_from_db();self.assertEqual(self.charge.error_code,'')
        reconcile_payment(self.provider_return());self.assertEqual(self.refund_sum(),-36002)

    def test_legacy_mp_link_credits_existing_negative_entry_without_posting_again(self):
        refund=self.legacy();reconcile_payment(self.provider_return());self.mock_provider()
        entry_ids=set(FinancialEntry.objects.values_list('pk',flat=True))
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('MERCADO_PAGO')).status_code,302)
        refund.refresh_from_db();self.order.refresh_from_db()
        self.assertEqual((refund.channel,refund.state),('MERCADO_PAGO','SUCCEEDED'))
        self.assertEqual(self.refund_sum(),-18001);self.assertEqual(Refund.objects.count(),1)
        self.assertEqual(set(FinancialEntry.objects.values_list('pk',flat=True)),entry_ids)
        self.assertEqual(PaymentTransaction.objects.get().posted_refund_cents,18001)
        self.assertEqual(self.order.status,'REFUNDED')
        reconcile_payment(self.provider_return());self.assertEqual(self.refund_sum(),-18001)

    def test_test_note_preserves_unknown_state_amount_ledger_and_never_calls_mp(self):
        refund=self.legacy();old_status=refund.state;old_reference=refund.provider_ref
        before=list(FinancialEntry.objects.order_by('pk').values())
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('TEST_NOTE')).status_code,302)
        refund.refresh_from_db()
        self.assertEqual((refund.channel,refund.state,refund.amount_cents,refund.provider_ref),('UNKNOWN',old_status,18001,old_reference))
        self.assertIn('registro de teste, sem devolução real',refund.reason_notes)
        self.assertEqual(list(FinancialEntry.objects.order_by('pk').values()),before);self.http.assert_not_called()
        self.assertTrue(AuditLog.objects.filter(action='LEGACY_REFUND_TEST_NOTED').exists())

    def test_mp_link_rejects_insufficient_authenticated_refund(self):
        refund=self.legacy();self.mock_provider(self.provider_return(90))
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('MERCADO_PAGO')).status_code,400)
        refund.refresh_from_db();self.assertEqual(refund.channel,'UNKNOWN');self.assertEqual(self.refund_sum(),-18001)

    def test_pending_review_has_refresh_path_after_external_saved_but_network_failed(self):
        refund=self.legacy();reconcile_payment(self.provider_return());self.http.side_effect=requests.Timeout()
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('EXTERNAL')).status_code,302)
        refund.refresh_from_db();self.assertEqual(refund.channel,'EXTERNAL')
        self.assertContains(self.client.get(reverse('gestao:finance',args=[self.event.pk])),self.review_url(refund))
        self.mock_provider()
        self.assertEqual(self.client.post(self.review_url(refund),{'action':'refresh'}).status_code,302)
        self.assertEqual(self.refund_sum(),-36002)

    def test_fresh_event_lock_rejects_canceled_event_despite_stale_related_object(self):
        Event.objects.filter(pk=self.event.pk).update(status='CANCELED')
        self.assertNotEqual(self.order.event.status,'CANCELED')
        self.assertFalse(_commit_capacity(self.order,self.now))
        self.assertEqual(self.order.event.status,'CANCELED')

    def test_legacy_channel_permission_csrf_and_event_scope(self):
        from apps.identity.models import User,EventMembership
        refund=self.legacy()
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        self.assertEqual(strict.post(self.review_url(refund),self.classification('TEST_NOTE')).status_code,403)
        another=Event.objects.create(organization=self.event.organization,title='Outro canal',slug='outro-refund-channel',
            starts_at=self.event.starts_at,ends_at=self.event.ends_at,registration_opens_at=self.event.registration_opens_at,registration_closes_at=self.event.registration_closes_at)
        other_url=reverse('gestao:refund_channel_review',args=[another.pk,refund.pk])
        self.assertEqual(self.client.get(other_url).status_code,404)
        user=User.objects.create_user(email='channel-registration-agent@example.test')
        EventMembership.objects.create(user=user,event=self.event,role_code='REGISTRATION_AGENT');self.client.force_login(user)
        self.assertEqual(self.client.get(self.review_url(refund)).status_code,403)
        self.assertEqual(self.client.post(self.review_url(refund),self.classification('EXTERNAL')).status_code,403)
        EventMembership.objects.filter(user=user).update(role_code='FINANCE')
        self.assertEqual(self.client.get(self.review_url(refund)).status_code,200)
        self.assertEqual(self.client.get(other_url).status_code,403)

    def test_legacy_mp_link_audit_failure_rolls_back_channel_and_snapshot_credit(self):
        refund=self.legacy();self.mock_provider()
        with patch('apps.registrations.refund_channels.log_audit',side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.review_url(refund),self.classification('MERCADO_PAGO'))
        refund.refresh_from_db()
        self.assertEqual(refund.channel,'UNKNOWN');self.assertEqual(refund.state,'MANUAL_COMPLETED')
        self.assertEqual(PaymentTransaction.objects.get().posted_refund_cents,0)
        self.assertEqual(self.refund_sum(),-18001)


@override_settings(**fixtures.MP_TEST_SETTINGS,PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RefundProviderNetworkBoundaryTests(TransactionTestCase):
    setUp=fixtures.PaymentReconciliationTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment

    def test_authenticated_provider_get_runs_outside_application_atomic_block(self):
        from apps.identity.models import User
        from apps.registrations.refund_channels import confirm_provider_return
        reconcile_payment(self.payment())
        actor=User.objects.create_user(email='boundary-refund-owner@example.test',is_staff=True)
        def api_reply(*args,**kwargs):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            self.assertEqual(args[0],'GET')
            return fixtures.response(self.payment(status='refunded',transaction_amount_refunded=180.01))
        self.http.side_effect=api_reply
        confirm_provider_return(event=self.event,order_id=self.order.pk,actor=actor,
            data={'amount':18001,'reason':'Consulta fictícia de estorno sem movimentar dinheiro'})
        self.assertEqual(Refund.objects.get().channel,'MERCADO_PAGO')
