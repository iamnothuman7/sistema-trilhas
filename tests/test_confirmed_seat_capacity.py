import uuid
from datetime import timedelta
from django.test import TestCase,override_settings
from apps.catalog.models import CapacityResource,CapacityHold
from apps.checkout.models import Order,OrderLine
from apps.identity.models import Person
from apps.payments.models import ChargeAttempt
from apps.payments.services import reconcile_payment
from apps.registrations.models import Registration
from apps.reporting.selectors import get_event_kpi_summary
from tests import test_payments as fixtures


@override_settings(**fixtures.MP_TEST_SETTINGS)
class ConfirmedSeatCapacityTests(TestCase):
    setUp=fixtures.PaymentReconciliationTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment

    def test_only_confirmed_payment_occupies_dashboard_and_resource(self):
        self.assertEqual(self.capacity.available_count,10)
        self.assertEqual(get_event_kpi_summary(self.event)['vacancies_left'],10)
        reconcile_payment(self.payment('pending'))
        self.assertEqual(get_event_kpi_summary(self.event)['vacancies_left'],10)
        reconcile_payment(self.payment())
        self.capacity.refresh_from_db();self.assertEqual(self.capacity.available_count,9)
        self.assertEqual(get_event_kpi_summary(self.event)['vacancies_left'],9)

    def test_many_pending_do_not_block_last_seat_but_second_approval_needs_review(self):
        self.capacity.capacity_total=1;self.capacity.reserved_count=2;self.capacity.save()
        other=Order.objects.create(event=self.event,merchant_reference='LAST-SEAT-2',purchaser_name='Outro piloto',purchaser_email='other@example.test',
            status='AWAITING_PAYMENT',total_cents=18001,gross_cents=18001,expires_at=self.now+timedelta(minutes=35))
        line=OrderLine.objects.create(order=other,product=self.product,unit_cents=18001,net_cents=18001,intended_person_name='Outro piloto')
        reg=Registration.objects.create(event=self.event,person=Person.objects.create(full_name='Outro piloto'),source_order_line=line)
        CapacityHold.objects.create(resource=self.capacity,order_id=other.pk,order_line_id=line.pk,quantity=1,expires_at=other.expires_at,idempotency_key=str(uuid.uuid4()))
        charge=ChargeAttempt.objects.create(order=other,amount_cents=18001,payment_method='pix',state='PENDING',idempotency_key=str(uuid.uuid4()))
        reconcile_payment(self.payment())
        self.order.refresh_from_db();self.assertEqual(self.order.status,'PAID')
        reconcile_payment(self.payment(id=998878,external_reference=other.merchant_reference,metadata={'order_id':str(other.pk),'charge_attempt_id':str(charge.pk)}))
        other.refresh_from_db();reg.refresh_from_db();self.capacity.refresh_from_db()
        self.assertEqual(other.status,'PAID_NEEDS_REVIEW');self.assertNotEqual(reg.commercial_state,'ACTIVE')
        self.assertEqual(self.capacity.committed_count,1);self.assertEqual(self.capacity.available_count,0)

    def test_pending_stock_still_applies_to_products_and_raffle_numbers(self):
        for kind in ['ADDON_TOTAL','RAFFLE_TOTAL']:
            resource=CapacityResource(type=kind,capacity_total=10,reserved_count=4,committed_count=2)
            self.assertEqual(resource.available_count,4)
