from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.db import transaction
from django.utils import timezone
from django.urls import reverse
from apps.catalog.models import ProductVariant,CapacityHold
from apps.catalog.stock import configure_shirt_stock,stock_version,shirt_rows
from apps.checkout.reservations import reserve_order
from apps.payments.services import reconcile_payment,_release_capacity,_suspend_registrations,expire_order
from apps.registrations.models import Entitlement
from tests import test_payments as fixtures


@override_settings(**fixtures.MP_TEST_SETTINGS)
class ShirtStockTests(TestCase):
    setUp=fixtures.PaymentReconciliationTests.setUp
    payment=fixtures.PaymentReconciliationTests.payment

    def configure(self,total=1):
        self.variant=ProductVariant.objects.create(product=self.product,code='M',label='M')
        self.line.variant=self.variant;self.line.save(update_fields=['variant'])
        self.registration.shirt_size='M';self.registration.save(update_fields=['shirt_size'])
        return configure_shirt_stock(event=self.event,variant_id=self.variant.pk,total=total,version='new',actor=None)

    def test_reservation_approval_replay_refund_never_double_counts(self):
        stock=self.configure()
        self.assertEqual((stock.reserved_count,stock.committed_count,stock.available_count),(1,0,0))
        reconcile_payment(self.payment('pending'))
        reconcile_payment(self.payment())
        reconcile_payment(self.payment())
        stock.refresh_from_db()
        self.assertEqual((stock.reserved_count,stock.committed_count,stock.available_count),(0,1,0))
        reconcile_payment(self.payment('refunded',date_last_updated=(self.now+timedelta(seconds=2)).isoformat()))
        stock.refresh_from_db();self.assertEqual(stock.available_count,1)
        self.assertEqual(shirt_rows(self.event)[0]['confirmed'],0)

    def test_delivered_shirt_retained_after_refund(self):
        stock=self.configure();reconcile_payment(self.payment())
        Entitlement.objects.filter(registration=self.registration,benefit_type='CAMISETA').update(quantity_used=1,state='CONSUMED')
        reconcile_payment(self.payment('refunded',date_last_updated=(self.now+timedelta(seconds=2)).isoformat()))
        stock.refresh_from_db();self.assertEqual(stock.available_count,0)
        self.assertEqual(stock.holds.get().status,'RETAINED')
        with transaction.atomic():_release_capacity(self.order,include_committed=True)
        stock.refresh_from_db();self.assertEqual(stock.available_count,0)
        rows=shirt_rows(self.event);self.assertEqual((rows[0]['delivered'],rows[0]['confirmed']),(1,0))

    def test_missing_shirt_hold_stops_confirmation_for_manual_review(self):
        stock=self.configure()
        stock.holds.all().delete()
        reconcile_payment(self.payment())
        self.order.refresh_from_db();self.registration.refresh_from_db()
        self.assertEqual(self.order.status,'PAID_NEEDS_REVIEW')
        self.assertNotEqual(self.registration.commercial_state,'ACTIVE')

    def test_identified_test_deliveries_do_not_retain_real_stock(self):
        stock=self.configure();reconcile_payment(self.payment())
        Entitlement.objects.filter(registration=self.registration,benefit_type='CAMISETA').update(quantity_used=1,state='CONSUMED')
        reconcile_payment(self.payment('refunded',date_last_updated=(self.now+timedelta(seconds=2)).isoformat()))
        stock.refresh_from_db();self.assertEqual(stock.holds.get().status,'RETAINED')
        self.order.refresh_from_db();self.order.is_test=True;self.order.save(update_fields=['is_test'])
        with transaction.atomic():_release_capacity(self.order,include_committed=True)
        stock.refresh_from_db();self.assertEqual(stock.available_count,1)
        self.assertEqual(shirt_rows(self.event)[0]['delivered'],0)

    def test_expired_shirt_and_orphan_counter_repaired(self):
        stock=self.configure()
        self.order.expires_at=self.now-timedelta(minutes=1);self.order.save(update_fields=['expires_at'])
        expire_order(self.order.pk);stock.refresh_from_db();self.assertEqual(stock.available_count,1)
        stock.reserved_count=6;stock.save()
        stock=configure_shirt_stock(event=self.event,variant_id=self.variant.pk,total=1,version=stock_version(stock),actor=None)
        self.assertEqual(stock.available_count,1)

    def test_reducing_below_actual_occupancy_and_stale_edit_blocked(self):
        stock=self.configure()
        with self.assertRaises(ValidationError):configure_shirt_stock(event=self.event,variant_id=self.variant.pk,total=0,version=stock_version(stock),actor=None)
        with self.assertRaises(ValidationError):configure_shirt_stock(event=self.event,variant_id=self.variant.pk,total=2,version='stale',actor=None)

    def test_last_unit_cannot_be_reserved_twice(self):
        from apps.catalog.stock import reserve_shirt
        stock=self.configure()
        with self.assertRaises(ValidationError),transaction.atomic():
            reserve_shirt(event=self.event,variant=self.variant,line=self.line,expires_at=self.order.expires_at)
        stock.refresh_from_db();self.assertEqual(stock.reserved_count,1)

    def test_configure_screen_keeps_event_permissions(self):
        from apps.identity.models import User
        stock=self.configure()
        user=User.objects.create_user(email='stock@example.test',is_staff=True)
        self.client.force_login(user)
        response=self.client.get(reverse('gestao:shirt_stock',args=[self.event.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Cadastrar modelo ou tamanho')
