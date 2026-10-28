"""Invalid coupons never charge full price; abandoned unpaid uses return once."""
import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Coupon, Product, Lot, CapacityHold, CapacityResource
from apps.checkout.coupons import release_unpaid_coupon_usage
from apps.checkout.models import Order, OrderCoupon
from apps.checkout.reservations import reserve_order
from apps.core.models import Organization
from apps.events.models import Event
from apps.finance.models import FinancialEntry
from apps.identity.models import User, Person
from apps.payments.models import ChargeAttempt
from apps.payments.services import expire_order
from apps.registrations.models import Registration
from apps.registrations.views_cleanup import archive_unpaid_order


class CouponUsageLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organization.objects.create(name='Organização de cupons QA')
        now = timezone.now()
        cls.event = Event.objects.create(organization=org, title='Evento QA cupons', slug='lifecycle-cupom', status='PUBLISHED',
            starts_at=now+timedelta(days=10), ends_at=now+timedelta(days=11),
            registration_opens_at=now-timedelta(days=1), registration_closes_at=now+timedelta(days=9))
        cls.product = Product.objects.create(event=cls.event, name='Piloto QA', includes_shirt=False)
        cls.lot = Lot.objects.create(event=cls.event, product=cls.product, title='Oferta QA', price_cents=12000,
            opens_at=now-timedelta(days=1), closes_at=now+timedelta(days=1), max_quantity=50)
        cls.coupon = Coupon.objects.create(event=cls.event, code_upper='CUPOMQA', kind='FIXED', value_cents=2000,
            used_count=3, max_uses=10, valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1))
        cls.user = User.objects.create_user(email='coupon-use@example.test', full_name='Comprador QA', is_staff=True)

    def reserve(self, code='CUPOMQA', cpf='52998224725'):
        return reserve_order(event=self.event, purchaser_user=self.user, coupon_code=code,
            buyer={'name':'Comprador QA','email':self.user.email,'phone':'88999998888','document':'00000000191'},
            participants=[dict(name='Piloto QA', cpf=cpf, email='piloto@example.test', phone='88999998888',
                city='Tianguá', state='CE', team='Morcego QA', product_id=self.product.pk, lot_id=self.lot.pk, shirt='')])

    def expire(self, order):
        order.expires_at = timezone.now()-timedelta(seconds=1)
        order.save(update_fields=['expires_at'])
        return expire_order(order.pk)

    def test_invalid_inactive_expired_future_and_exhausted_coupon_roll_back_everything(self):
        now = timezone.now()
        for changes, code in (({}, 'DESCONHECIDOQA'), ({'active':False}, 'CUPOMQA'),
                ({'valid_until':now-timedelta(seconds=1)}, 'CUPOMQA'),
                ({'valid_from':now+timedelta(hours=1)}, 'CUPOMQA'), ({'max_uses':3}, 'CUPOMQA')):
            with self.subTest(changes=changes):
                defaults=dict(active=True, valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1), max_uses=10)
                defaults.update(changes);Coupon.objects.filter(pk=self.coupon.pk).update(**defaults)
                with self.assertRaisesMessage(ValidationError, 'Revise o código ou remova o cupom'):
                    self.reserve(code)
                self.assertEqual((Order.objects.count(), OrderCoupon.objects.count(), Registration.objects.count(), Person.objects.count(), CapacityHold.objects.count(), CapacityResource.objects.count()), (0, 0, 0, 0, 0, 0))
                self.coupon.refresh_from_db(); self.assertEqual(self.coupon.used_count, 3)

    def test_browser_checkout_keeps_entered_data_and_shows_coupon_error_without_an_order(self):
        url = reverse('checkout:start_registration', args=[self.event.slug])
        response = self.client.post(url, dict(purchaser_name='Comprador QA', purchaser_email=self.user.email,
            purchaser_phone='88999998888', purchaser_document='00000000191', coupon_code='INVALIDOQA',
            **{'participant_name[]':['Piloto QA'], 'participant_document[]':['52998224725'],
                'participant_product[]':[str(self.product.pk)], 'participant_lot[]':[str(self.lot.pk)],
                'participant_shirt[]':[''], 'participant_city[]':['Tianguá'], 'participant_state[]':['CE']}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revise o código ou remova o cupom')
        self.assertContains(response, 'value="INVALIDOQA"')
        self.assertContains(response, 'value="Piloto QA"')
        self.assertFalse(Order.objects.exists())

    def test_expired_use_returns_once_even_after_coupon_is_renamed(self):
        order = self.reserve()
        self.coupon.refresh_from_db(); self.assertEqual(self.coupon.used_count, 4)
        self.coupon.code_upper = 'NOVOQA'; self.coupon.save()
        self.expire(order); self.expire(order)
        self.assertFalse(release_unpaid_coupon_usage(order))
        self.coupon.refresh_from_db(); applied = OrderCoupon.objects.get(order=order)
        self.assertEqual(self.coupon.used_count, 3)
        self.assertIsNotNone(applied.usage_released_at)
        self.assertEqual(applied.display_code, 'CUPOMQA')
        self.assertEqual(applied.coupon_id, self.coupon.pk)

    def test_archived_unpaid_use_returns_once_and_preserves_coupon_application(self):
        order = self.reserve()
        archive_unpaid_order(order.pk, actor=self.user)
        archive_unpaid_order(order.pk, actor=self.user)
        self.coupon.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.coupon.used_count, order.status), (3, 'CANCELED'))
        self.assertIsNotNone(OrderCoupon.objects.get(order=order).usage_released_at)

    def test_paid_refunded_or_receipt_evidence_never_returns_coupon_use(self):
        order = self.reserve()
        for status in ['PAID', 'REFUNDED', 'PARTIALLY_REFUNDED', 'PAID_NEEDS_REVIEW']:
            order.status = status; order.save(update_fields=['status'])
            self.assertFalse(release_unpaid_coupon_usage(order))
        order.status='EXPIRED'; order.save(update_fields=['status'])
        FinancialEntry.objects.create(event=self.event, order=order, kind='RECEITA_INSCRICAO', amount_cents_signed=10000,
            source_reference='COUPON-PROOF-QA', description='Recebimento histórico de teste')
        self.assertFalse(release_unpaid_coupon_usage(order))
        self.coupon.refresh_from_db(); self.assertEqual(self.coupon.used_count, 4)
        self.assertIsNone(OrderCoupon.objects.get(order=order).usage_released_at)

    def test_migration_returns_only_proven_unpaid_and_preserves_refunded_uses(self):
        now=timezone.now()
        rows={}
        for index,status in enumerate(['EXPIRED','CANCELED','PAID','REFUNDED','EXPIRED']):
            order=Order.objects.create(event=self.event,merchant_reference=f'LEGACY-USAGE-QA-{index}',status=status,expires_at=now)
            rows[index]=OrderCoupon.objects.create(order=order,coupon=self.coupon,discount_applied_cents=2000)
        self.coupon.used_count=5;self.coupon.save(update_fields=['used_count'])
        ChargeAttempt.objects.create(order_id=rows[4].order_id,idempotency_key='UNCERTAIN-COUPON-QA',amount_cents=10000,state='UNKNOWN')
        migration=importlib.import_module('apps.checkout.migrations.0005_ordercoupon_coupon_code_snapshot')
        migration.release_proven_unpaid_expired_uses(apps,SimpleNamespace(connection=connection))
        migration.release_proven_unpaid_expired_uses(apps,SimpleNamespace(connection=connection))
        self.coupon.refresh_from_db();self.assertEqual(self.coupon.used_count,3)
        for index,applied in rows.items():
            applied.refresh_from_db()
            self.assertEqual(applied.usage_released_at is not None,index in [0,1])
