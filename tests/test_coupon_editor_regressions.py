"""Coupon editing must validate only the chosen discount and preserve order history."""
import csv
import importlib
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.catalog.forms_management import edit_version
from apps.catalog.models import Coupon, Lot, Product
from apps.checkout.models import Order, OrderCoupon, OrderLine
from apps.checkout.reservations import reserve_order
from apps.core.models import AuditLog, Organization
from apps.events.models import Event
from apps.identity.models import User
from apps.reporting.exports import export_orders_csv


class CouponEditorRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Organização dos cupons de teste')
        now = timezone.now()
        dates = dict(starts_at=now + timedelta(days=10), ends_at=now + timedelta(days=11),
            registration_opens_at=now - timedelta(days=1), registration_closes_at=now + timedelta(days=9))
        cls.event = Event.objects.create(organization=cls.org, title='Evento dos cupons', slug='evento-cupons-regressao', **dates)
        cls.other = Event.objects.create(organization=cls.org, title='Outro evento dos cupons', slug='outro-evento-cupons-regressao', **dates)
        cls.manager = User.objects.create_user(email='coupon-manager@example.test', full_name='Gestão de cupons', is_staff=True)

    def setUp(self):
        self.client.force_login(self.manager)

    def coupon(self, **changes):
        values = dict(event=self.event, code_upper='CUPOMQA', kind='FIXED', value_cents=11900, percent_off=10,
            used_count=3, max_uses=50, active=True,
            valid_from=datetime(2026, 5, 10, 10, tzinfo=dt_timezone.utc),
            valid_until=datetime(2026, 11, 30, 15, tzinfo=dt_timezone.utc))
        values.update(changes)
        return Coupon.objects.create(**values)

    def url(self, coupon=None, event=None):
        kwargs = {'event_id': (event or self.event).pk, 'kind': 'cupom'}
        if coupon:
            kwargs['object_id'] = coupon.pk
        return reverse('gestao:catalog_edit' if coupon else 'gestao:catalog_create', kwargs=kwargs)

    def payload(self, coupon, **changes):
        zone = ZoneInfo(self.event.timezone)
        values = dict(version=edit_version(coupon), code_upper=coupon.code_upper, kind=coupon.kind,
            amount='119,00', percent_off=str(coupon.percent_off), max_uses=str(coupon.max_uses), active='on',
            valid_from=timezone.localtime(coupon.valid_from, zone).strftime('%Y-%m-%dT%H:%M'),
            valid_until=timezone.localtime(coupon.valid_until, zone).strftime('%Y-%m-%dT%H:%M'))
        values.update(changes)
        return values

    def test_production_shaped_fixed_used_coupon_edits_values_and_dates_through_htmx(self):
        coupon = self.coupon()
        response = self.client.get(self.url(coupon))
        self.assertContains(response, 'value="2026-05-10T07:00"')
        self.assertContains(response, 'value="2026-11-30T12:00"')
        self.assertFalse(response.context['form'].fields['code_upper'].disabled)
        self.assertContains(response, 'Você pode renomear mesmo após o uso.')
        changed = self.payload(coupon, amount='118,50', percent_off='', valid_until='2026-12-31T16:30', max_uses='60')
        response = self.client.post(self.url(coupon), changed, HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['HX-Location'], reverse('gestao:catalog', args=[self.event.pk]))
        coupon.refresh_from_db()
        self.assertEqual((coupon.value_cents, coupon.used_count, coupon.max_uses, coupon.percent_off), (11850, 3, 60, 10))
        self.assertEqual(coupon.valid_until, datetime(2026, 12, 31, 19, 30, tzinfo=dt_timezone.utc))

    def test_fixed_coupon_with_legacy_zero_percentage_does_not_require_a_percentage(self):
        coupon = self.coupon(percent_off=0)
        data = self.payload(coupon, amount='25,50')
        data.pop('percent_off')
        self.assertEqual(self.client.post(self.url(coupon), data).status_code, 302)
        coupon.refresh_from_db()
        self.assertEqual((coupon.value_cents, coupon.percent_off), (2550, 0))

    def test_new_fixed_coupon_requires_only_its_money_value(self):
        data = self.payload(self.coupon(code_upper='ORIGINALQA'), code_upper='NOVOQA', version='', percent_off='', amount='7,25')
        response = self.client.post(self.url(), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Coupon.objects.get(code_upper='NOVOQA').value_cents, 725)

    def test_percent_coupon_with_zero_or_empty_fixed_value_edits_normally(self):
        coupon = self.coupon(kind='PERCENT', value_cents=0, percent_off=15)
        for inactive_value in ('', '0,00', 'campo inaplicável'):
            with self.subTest(inactive_value=inactive_value):
                coupon.refresh_from_db()
                response = self.client.post(self.url(coupon), self.payload(coupon, amount=inactive_value, percent_off='18'))
                self.assertEqual(response.status_code, 302)
                coupon.refresh_from_db()
                self.assertEqual((coupon.percent_off, coupon.value_cents), (18, 0))

    def test_selected_discount_is_required_and_percent_range_is_enforced(self):
        coupon = self.coupon(kind='PERCENT')
        for value in ('', '0', '-1', '101', '1,5', 'abc'):
            with self.subTest(percent=value):
                response = self.client.post(self.url(coupon), self.payload(coupon, percent_off=value))
                self.assertEqual(response.status_code, 400)
                self.assertIn('percent_off', response.context['form'].errors)
        coupon.refresh_from_db()
        self.assertEqual(coupon.percent_off, 10)
        for value in ('', '0', '12,345'):
            with self.subTest(amount=value):
                response = self.client.post(self.url(coupon), self.payload(coupon, kind='FIXED', amount=value))
                self.assertEqual(response.status_code, 400)
                self.assertIn('amount', response.context['form'].errors)

    def test_type_can_change_both_ways_without_using_the_other_discount(self):
        coupon = self.coupon(percent_off=0)
        response = self.client.post(self.url(coupon), self.payload(coupon, kind='PERCENT', percent_off='12', amount=''))
        self.assertEqual(response.status_code, 302)
        coupon.refresh_from_db()
        self.assertEqual((coupon.kind, coupon.percent_off, coupon.value_cents), ('PERCENT', 12, 11900))
        response = self.client.post(self.url(coupon), self.payload(coupon, kind='FIXED', amount='5,50', percent_off=''))
        self.assertEqual(response.status_code, 302)
        coupon.refresh_from_db()
        self.assertEqual((coupon.kind, coupon.percent_off, coupon.value_cents), ('FIXED', 12, 550))

    def test_used_coupon_can_be_renamed_while_order_values_and_original_code_are_preserved(self):
        coupon = self.coupon()
        order = Order.objects.create(event=self.event, merchant_reference='HISTORICO-CUPOM-QA', expires_at=timezone.now(),
            status='PAID', gross_cents=12000, discount_cents=11900, total_cents=100)
        line = OrderLine.objects.create(order=order, unit_cents=12000, net_cents=100, item_snapshot_json={'price_cents': 12000})
        applied = OrderCoupon.objects.create(order=order, coupon=coupon, discount_applied_cents=11900)
        response = self.client.post(self.url(coupon), self.payload(coupon, code_upper='NAO-RENOMEAR', amount='20,00'))
        self.assertEqual(response.status_code, 302)
        coupon.refresh_from_db(); order.refresh_from_db(); line.refresh_from_db(); applied.refresh_from_db()
        self.assertEqual(coupon.code_upper, 'NAO-RENOMEAR')
        self.assertEqual(applied.display_code, 'CUPOMQA')
        self.assertEqual(order.applied_coupon_code, 'CUPOMQA')
        self.assertEqual((coupon.used_count, order.discount_cents, order.total_cents, line.net_cents, applied.discount_applied_cents), (3, 11900, 100, 100, 11900))

    def test_inactive_expired_and_future_coupons_remain_editable(self):
        now = timezone.now()
        for index, (opening, closing) in enumerate(((now-timedelta(days=10), now-timedelta(days=1)), (now+timedelta(days=1), now+timedelta(days=10)))):
            coupon = self.coupon(code_upper=f'DATASQA{index}', active=False, valid_from=opening, valid_until=closing)
            data = self.payload(coupon, amount='19,90')
            data.pop('active')
            response = self.client.post(self.url(coupon), data)
            self.assertEqual(response.status_code, 302)
            coupon.refresh_from_db()
            self.assertEqual(coupon.value_cents, 1990)
            self.assertFalse(coupon.active)

    def test_usage_limit_and_validity_order_still_protect_coupon(self):
        coupon = self.coupon()
        for changes, field in (({'max_uses':'2'}, 'max_uses'), ({'valid_until':'2026-01-01T00:00'}, 'valid_until')):
            with self.subTest(field=field):
                response = self.client.post(self.url(coupon), self.payload(coupon, **changes))
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.context['form'].errors)
        self.assertEqual(self.client.post(self.url(coupon), self.payload(coupon, max_uses='3')).status_code, 302)

    def test_stale_editor_and_cross_event_never_overwrite_coupon(self):
        coupon = self.coupon()
        stale = self.payload(coupon, amount='1,00')
        coupon.value_cents = 2000; coupon.save(update_fields=['value_cents'])
        response = self.client.post(self.url(coupon), stale, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Este cadastro foi alterado em outra sessão.', status_code=400)
        self.assertContains(response, 'data-workspace-reset', status_code=400)
        self.assertEqual(self.client.get(self.url(coupon, event=self.other)).status_code, 404)
        coupon.refresh_from_db()
        self.assertEqual(coupon.value_cents, 2000)
        self.assertFalse(AuditLog.objects.filter(action='CATALOG_UPDATED').exists())

    def test_event_timezone_controls_coupon_inputs_and_persisted_dates(self):
        self.event.timezone = 'America/Manaus'; self.event.save(update_fields=['timezone'])
        coupon = self.coupon(used_count=0)
        response = self.client.get(self.url(coupon))
        self.assertContains(response, 'value="2026-05-10T06:00"')
        data = self.payload(coupon, valid_until='2026-12-31T16:30')
        self.assertEqual(self.client.post(self.url(coupon), data).status_code, 302)
        coupon.refresh_from_db()
        self.assertEqual(coupon.valid_until, datetime(2026, 12, 31, 20, 30, tzinfo=dt_timezone.utc))

    def test_rename_keeps_existing_application_and_only_new_code_discounts_new_orders(self):
        now = timezone.now()
        self.event.status = 'PUBLISHED'; self.event.save(update_fields=['status'])
        product = Product.objects.create(event=self.event, name='Piloto QA', includes_shirt=False)
        lot = Lot.objects.create(event=self.event, product=product, title='Oferta QA', price_cents=12000,
            opens_at=now-timedelta(days=1), closes_at=now+timedelta(days=1), max_quantity=50)
        coupon = self.coupon(valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1))

        def buy(cpf, code):
            return reserve_order(event=self.event, purchaser_user=self.manager, coupon_code=code,
                buyer={'name':'Comprador QA','email':self.manager.email,'phone':'88999998888','document':'00000000191'},
                participants=[dict(name='Piloto QA', cpf=cpf, email='piloto@example.test', phone='88999998888',
                    city='Tianguá', state='CE', team='Morcego QA', product_id=product.pk, lot_id=lot.pk, shirt='')])

        original = buy('52998224725', 'CUPOMQA')
        before_application = original.applied_coupon
        before_snapshot = original.lines.get().item_snapshot_json.copy()
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 4)
        response = self.client.post(self.url(coupon), self.payload(coupon, code_upper=' novoqa '))
        self.assertEqual(response.status_code, 302)
        coupon.refresh_from_db(); original.refresh_from_db(); before_application.refresh_from_db()
        self.assertEqual(coupon.code_upper, 'NOVOQA')
        self.assertEqual((coupon.used_count, before_application.coupon_id), (4, coupon.pk))
        self.assertEqual((original.discount_cents, original.total_cents, before_application.display_code), (11900, 100, 'CUPOMQA'))
        self.assertEqual(original.lines.get().item_snapshot_json, before_snapshot)

        new_order = buy('11144477735', 'NOVOQA')
        with self.assertRaisesMessage(ValidationError, 'Este cupom não existe'):
            buy('00000000191', 'CUPOMQA')
        coupon.refresh_from_db()
        self.assertEqual(new_order.discount_cents, 11900)
        self.assertEqual(new_order.applied_coupon_code, 'NOVOQA')
        self.assertEqual((coupon.used_count, Coupon.objects.filter(event=self.event).count(), OrderCoupon.objects.count(), Order.objects.count()), (5, 1, 2, 2))

        history = self.client.get(reverse('minha_conta:registration_detail', args=[original.lines.get().registration.pk]))
        self.assertContains(history, 'CUPOMQA')
        self.assertNotContains(history, 'NOVOQA')
        rows = list(csv.DictReader(io.StringIO(export_orders_csv(self.event).content.decode('utf-8-sig')), delimiter=';'))
        codes = {row['Pedido']: row['Cupom usado na compra'] for row in rows}
        self.assertEqual(codes[original.merchant_reference], 'CUPOMQA')
        self.assertEqual(codes[new_order.merchant_reference], 'NOVOQA')

    def test_duplicate_code_in_same_event_is_rejected_but_another_event_is_independent(self):
        coupon = self.coupon()
        self.coupon(code_upper='EXISTENTEQA')
        response = self.client.post(self.url(coupon), self.payload(coupon, code_upper='existenteqa'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('code_upper', response.context['form'].errors)
        coupon.refresh_from_db(); self.assertEqual(coupon.code_upper, 'CUPOMQA')
        self.coupon(event=self.other, code_upper='OUTROEVENTOQA')
        self.assertEqual(self.client.post(self.url(coupon), self.payload(coupon, code_upper='OUTROEVENTOQA')).status_code, 302)

    def test_snapshot_is_immutable_and_legacy_blank_is_preserved_before_rename(self):
        coupon = self.coupon()
        order = Order.objects.create(event=self.event, merchant_reference='LEGACY-COUPON-QA', expires_at=timezone.now())
        applied = OrderCoupon.objects.create(order=order, coupon=coupon, discount_applied_cents=11900)
        self.assertEqual(applied.coupon_code_snapshot, 'CUPOMQA')
        applied.coupon_code_snapshot = 'ALTERADO'
        with self.assertRaises(ValidationError):
            applied.save()
        OrderCoupon.objects.filter(pk=applied.pk).update(coupon_code_snapshot='')
        applied.refresh_from_db()
        self.assertEqual(applied.display_code, 'CUPOMQA')
        coupon.code_upper = 'CODIGONOVOQA'; coupon.save()
        applied.refresh_from_db()
        self.assertEqual((applied.coupon_code_snapshot, applied.display_code), ('CUPOMQA', 'CUPOMQA'))

    def test_data_migration_backfills_existing_application_code(self):
        coupon = self.coupon()
        order = Order.objects.create(event=self.event, merchant_reference='MIGRATION-COUPON-QA', expires_at=timezone.now())
        applied = OrderCoupon.objects.create(order=order, coupon=coupon, discount_applied_cents=11900)
        OrderCoupon.objects.filter(pk=applied.pk).update(coupon_code_snapshot='')
        migration = importlib.import_module('apps.checkout.migrations.0005_ordercoupon_coupon_code_snapshot')
        migration.preserve_existing_codes(apps, SimpleNamespace(connection=connection))
        applied.refresh_from_db()
        self.assertEqual(applied.coupon_code_snapshot, 'CUPOMQA')
