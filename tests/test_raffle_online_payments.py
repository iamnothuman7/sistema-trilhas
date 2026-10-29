import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import CapacityResource
from apps.checkout.models import Order,OrderStatus
from apps.checkout.raffle_purchases import reserve_raffle_numbers
from apps.events.models import MotorcycleRaffle,LegalDocumentVersion
from apps.events.models_raffles import RaffleTicket
from apps.events.forms_raffles import MotorcycleRaffleForm
from apps.events.raffle_operations import confirmed_registration_tickets
from apps.finance.models import FinancialEntry
from apps.identity.models import User,UserPersonLink
from apps.payments.models import ChargeAttempt
from apps.payments.services import reconcile_payment,expire_order
from apps.registrations.models import Registration,TermsAcceptance
from apps.registrations.management import cancel_paid_order
from tests import test_payments as fixtures


@override_settings(**fixtures.MP_TEST_SETTINGS)
class OnlineRaffleTests(TestCase):
    setUp=fixtures.PaymentReconciliationTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment

    def prepare(self,enabled=True):
        self.event.status='PUBLISHED';self.event.save()
        self.user=User.objects.create_user(email='raffle-pilot@example.test',full_name='Piloto',is_staff=True)
        self.order.purchaser_user=self.user;self.order.save()
        self.raffle=MotorcycleRaffle.objects.create(event=self.event,is_public=True,online_sales_enabled=enabled,operation_review_confirmed=True,
            quota_price_cents=500,quota_quantity=10,draw_at=self.now+timedelta(days=10),rules_text='Regulamento fictício exclusivamente para testes locais.',
            authorization_reference='REFERENCIA-FICTICIA-QA',provider_review_reference='PROTOCOLO-FICTICIO-QA')
        reconcile_payment(self.payment());self.registration.refresh_from_db();self.order.refresh_from_db()
        UserPersonLink.objects.create(user=self.user,person=self.person)
        self.client.force_login(self.user)

    def reserve(self,quantity=3,key=None):
        return reserve_raffle_numbers(registration_id=self.registration.pk,user=self.user,quantity=quantity,request_key=key or str(uuid.uuid4()),rules_accepted=True)

    def approve(self,order,status='approved',**changes):
        charge=ChargeAttempt.objects.filter(order=order).first() or ChargeAttempt.objects.create(order=order,idempotency_key=str(uuid.uuid4()),
            amount_cents=order.total_cents,payment_method='pix',state='PENDING',pix_expiration=order.expires_at)
        data=self.payment(status,id=int(order.pk.int%1000000000)+1000000000,transaction_amount=order.total_cents/100,
            external_reference=order.merchant_reference,metadata={'order_id':str(order.pk),'charge_attempt_id':str(charge.pk)},
            transaction_amount_refunded=order.total_cents/100 if status=='refunded' else 0,
            date_last_updated=(self.now+timedelta(minutes=1 if status=='approved' else 2)).isoformat(),fee_details=[{'amount':.15,'fee_payer':'collector'}])
        data.update(changes);reconcile_payment(data);order.refresh_from_db();return charge,data

    def test_draft_or_incomplete_never_creates_a_charge_or_purchase(self):
        self.prepare(enabled=False)
        with self.assertRaises(ValidationError):self.reserve()
        self.raffle.online_sales_enabled=True;self.raffle.quota_price_cents=None;self.raffle.save()
        with self.assertRaises(ValidationError):self.reserve()
        self.assertFalse(Order.objects.filter(lines__raffle=self.raffle).exists())

    def test_reservation_idempotent_stock_and_price_authoritative(self):
        self.prepare();key=str(uuid.uuid4());order=self.reserve(key=key)
        self.assertEqual(self.reserve(key=key).pk,order.pk)
        self.assertEqual(order.total_cents,1500)
        self.assertFalse(RaffleTicket.objects.filter(source='ONLINE').exists())
        resource=CapacityResource.objects.get(type='RAFFLE_TOTAL');self.assertEqual(resource.reserved_count,3)
        with self.assertRaises(ValidationError):self.reserve(quantity=2,key=key)
        with self.assertRaises(ValidationError):self.reserve(quantity=8)
        self.assertEqual(Order.objects.filter(lines__raffle=self.raffle).count(),1)

    def test_pix_approval_issues_numbers_once_and_uses_general_and_raffle_ledger(self):
        self.prepare();order=self.reserve();charge,data=self.approve(order);reconcile_payment(data)
        self.assertEqual(order.status,'PAID')
        self.assertEqual(list(confirmed_registration_tickets(self.registration).values_list('number',flat=True)),[1,2,3,4])
        self.assertEqual(Registration.objects.count(),1)
        self.assertEqual(FinancialEntry.objects.filter(order=order,kind='RECEITA_SORTEIO',raffle=self.raffle).get().amount_cents_signed,1500)
        self.assertEqual(FinancialEntry.objects.filter(order=order).count(),2)
        resource=CapacityResource.objects.get(type='RAFFLE_TOTAL');self.assertEqual((resource.reserved_count,resource.committed_count),(0,3))
        result=self.client.get(reverse('checkout:order_result',args=[order.pk]));self.assertContains(result,'Seus números estão confirmados!')
        finance=self.client.get(reverse('gestao:raffle_finance',args=[self.event.pk]))
        self.assertEqual(finance.context['totals']['received'],'15,00');self.assertEqual(finance.context['totals']['fees'],'0,15')
        self.assertEqual(finance.context['online_count'],3)

    def test_card_reuses_reconciliation_pipeline(self):
        self.prepare();order=self.reserve(quantity=2)
        charge=ChargeAttempt.objects.create(order=order,idempotency_key=str(uuid.uuid4()),amount_cents=order.total_cents,
            payment_method='credit_card',state='PENDING')
        self.approve(order,payment_type_id='credit_card',payment_method_id='visa')
        self.assertEqual(order.status,'PAID');self.assertEqual(order.lines.get().raffle_tickets.count(),2)

    def test_provider_refund_only_cancels_extras_not_base_qr_or_included_number(self):
        self.prepare();order=self.reserve();self.approve(order);self.approve(order,status='refunded')
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertEqual(self.registration.raffle_ticket.state,'SOLD')
        self.assertEqual(RaffleTicket.objects.filter(source='ONLINE',state='VOID').count(),3)
        self.assertEqual(CapacityResource.objects.get(type='RAFFLE_TOTAL').committed_count,0)
        self.assertEqual(FinancialEntry.objects.filter(order=order,kind='ESTORNO_SORTEIO').get().amount_cents_signed,-1500)

    def test_expiration_releases_only_extra_reservation_without_assigning_numbers(self):
        self.prepare();order=self.reserve();order.expires_at=self.now-timedelta(seconds=1);order.save()
        expire_order(order.pk)
        self.assertEqual(CapacityResource.objects.get(type='RAFFLE_TOTAL').reserved_count,0)
        self.assertFalse(RaffleTicket.objects.filter(source='ONLINE').exists())
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'ACTIVE')

    def test_manual_return_of_extra_order_and_base_return_dependency(self):
        self.prepare();order=self.reserve();self.approve(order)
        data=dict(request_key=uuid.uuid4(),channel='EXTERNAL',amount=self.order.total_cents,returned_on=timezone.localdate(),reference='DEV-QA',reason='Devolução fictícia para teste local',confirmed=True)
        with self.assertRaises(ValidationError):cancel_paid_order(event=self.event,order_id=self.order.pk,data=data,actor=self.user)
        data.update(amount=1500)
        cancel_paid_order(event=self.event,order_id=order.pk,data=data,actor=self.user)
        self.registration.refresh_from_db();self.assertEqual(self.registration.commercial_state,'ACTIVE')
        self.assertEqual(self.registration.raffle_ticket.state,'SOLD')
        self.assertEqual(FinancialEntry.objects.filter(order=order,kind='ESTORNO_SORTEIO').count(),1)

    def test_disabled_after_reservation_honors_paid_existing_order(self):
        self.prepare();order=self.reserve();self.raffle.online_sales_enabled=False;self.raffle.save()
        self.approve(order);self.assertEqual(order.status,'PAID')
        self.assertEqual(order.lines.get().raffle_tickets.count(),3)

    def test_payment_arriving_after_recorded_draw_cannot_issue_new_numbers(self):
        from apps.events.raffle_results import record_draw_result
        self.prepare();order=self.reserve()
        self.raffle.draw_at=self.now-timedelta(minutes=1);self.raffle.save()
        record_draw_result(raffle=self.raffle,actor=self.user,numbers=[1],
            notes='Resultado fictício conferido no globo de teste.',confirmed=True)
        self.approve(order)
        self.assertEqual(order.status,'PAID_NEEDS_REVIEW')
        self.assertFalse(order.lines.get().raffle_tickets.exists())
        self.assertEqual(FinancialEntry.objects.filter(order=order,kind='RECEITA_SORTEIO').count(),1)

    def test_refunded_base_blocks_later_extra_payment_from_releasing_numbers(self):
        self.prepare();order=self.reserve()
        data=dict(request_key=uuid.uuid4(),channel='EXTERNAL',amount=self.order.total_cents,returned_on=timezone.localdate(),reference='DEV-QA',reason='Devolução fictícia para teste local',confirmed=True)
        cancel_paid_order(event=self.event,order_id=self.order.pk,data=data,actor=self.user)
        self.approve(order);self.assertEqual(order.status,'PAID_NEEDS_REVIEW')
        self.assertFalse(RaffleTicket.objects.filter(source='ONLINE').exists())

    def test_signed_request_and_explicit_rules_acceptance_required(self):
        self.prepare();url=reverse('checkout:raffle_numbers',args=[self.registration.pk])
        page=self.client.get(url);self.assertEqual(page.status_code,200)
        token=page.context['request_token']
        self.assertEqual(self.client.post(url,{'request_token':token,'quantity':'2'}).status_code,400)
        self.assertEqual(self.client.post(url,{'request_token':'forged','quantity':'2','rules_accepted':'yes'}).status_code,400)
        response=self.client.post(url,{'request_token':token,'quantity':'2','rules_accepted':'yes'})
        self.assertEqual(response.status_code,302)
        order=Order.objects.get(lines__raffle=self.raffle);self.assertEqual(order.total_cents,1000)

    def test_activation_requires_complete_settings_and_stock_cannot_be_reduced(self):
        self.prepare(enabled=False)
        payload=dict(title=self.raffle.title,motorcycle_count=1,paper_start=2000,paper_end=3000,is_public='on',online_sales_enabled='on')
        form=MotorcycleRaffleForm(payload,instance=self.raffle)
        self.assertFalse(form.is_valid());self.assertIn('online_sales_enabled',form.errors)
        self.raffle.refresh_from_db();self.raffle.online_sales_enabled=True;self.raffle.save();self.reserve()
        payload.update(quota_quantity=2,quota_price='5,00',draw_at=(self.now+timedelta(days=10)).strftime('%Y-%m-%dT%H:%M'),
            rules_text=self.raffle.rules_text,authorization_reference='QA',provider_review_reference='QA',operation_review_confirmed='on')
        form=MotorcycleRaffleForm(payload,instance=self.raffle)
        self.assertFalse(form.is_valid());self.assertIn('quota_quantity',form.errors)
