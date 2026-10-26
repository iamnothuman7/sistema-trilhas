from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import Product, Lot, ProductVariant, Coupon, CapacityResource
from apps.catalog.forms_management import edit_version
from apps.checkout.models import Order, OrderLine
from apps.events.models import Event
from apps.identity.models import User, EventMembership, RoleChoices
from tests import test_system as fixtures


class CatalogEditingTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def url(self, kind, item=None, event=None):
        kwargs = {'event_id': (event or self.event).pk, 'kind': kind}
        if item: kwargs['object_id'] = item.pk
        return reverse('gestao:catalog_edit' if item else 'gestao:catalog_create', kwargs=kwargs)

    def lot_data(self, **changes):
        from apps.reporting.selectors import event_timezone
        data = dict(version=edit_version(self.lot), title='Oferta editada', price='120,29', max_quantity=60, priority=1, active='on',
            opens_at=timezone.localtime(self.lot.opens_at, event_timezone(self.event)).strftime('%Y-%m-%dT%H:%M'),
            closes_at=timezone.localtime(self.lot.closes_at, event_timezone(self.event)).strftime('%Y-%m-%dT%H:%M'))
        data.update(changes)
        return data

    def test_edit_lot_preserves_orders_and_updates_capacity(self):
        self.client.force_login(self.user)
        order = Order.objects.create(event=self.event, merchant_reference='OLD-CATALOG', total_cents=18000, expires_at=timezone.now()+timedelta(hours=1))
        line = OrderLine.objects.create(order=order, product=self.product, lot=self.lot, unit_cents=18000, net_cents=18000, item_snapshot_json={'price_cents':18000})
        resource = CapacityResource.objects.create(event=self.event, type='LOT_TOTAL', reference_id=str(self.lot.pk), capacity_total=50, reserved_count=2, committed_count=3)
        response = self.client.post(self.url('lote', self.lot), self.lot_data(current_sold=999))
        self.assertEqual(response.status_code, 302)
        self.lot.refresh_from_db(); resource.refresh_from_db(); line.refresh_from_db()
        self.assertEqual((self.lot.price_cents, self.lot.current_sold, resource.capacity_total), (12029, 0, 60))
        self.assertEqual(line.net_cents, 18000)
        self.assertEqual(line.item_snapshot_json['price_cents'], 18000)

    def test_capacity_cannot_drop_below_reserved_and_committed(self):
        self.client.force_login(self.user)
        CapacityResource.objects.create(event=self.event, type='LOT_TOTAL', reference_id=str(self.lot.pk), capacity_total=50, reserved_count=3, committed_count=4)
        response = self.client.post(self.url('lote', self.lot), self.lot_data(max_quantity=6))
        self.assertEqual(response.status_code, 400)
        uppercase_url = self.url('lote', self.lot).replace(str(self.lot.pk), str(self.lot.pk).upper())
        self.assertEqual(self.client.post(uppercase_url, self.lot_data(max_quantity=6)).status_code, 400)
        self.lot.refresh_from_db(); self.assertEqual(self.lot.max_quantity, 50)

    def test_invalid_price_dates_and_stale_form_do_not_save(self):
        self.client.force_login(self.user)
        for changes in ({'price':'12,345'}, {'closes_at':'2000-01-01T00:00'}, {'version':'stale'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.url('lote', self.lot), self.lot_data(**changes)).status_code, 400)
        self.lot.refresh_from_db(); self.assertEqual(self.lot.price_cents, 18000)

    def test_create_product_lot_and_variant(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url('produto'), {'name':'Pacote novo', 'kind':'PILOTO', 'active':'on', 'includes_kit':'on'})
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name='Pacote novo')
        self.assertEqual(product.event, self.event)
        response = self.client.post(self.url('lote')+'?product='+str(product.pk), self.lot_data(version=''))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lot.objects.get(product=product).price_cents, 12029)
        url = self.url('tamanho')+'?product='+str(product.pk)
        self.assertEqual(self.client.post(url, {'code':'gg', 'label':'GG', 'extra_price':'10,00'}).status_code, 302)
        self.assertEqual(ProductVariant.objects.get(product=product).extra_price_cents, 1000)
        self.assertEqual(self.client.post(url, {'code':'GG', 'label':'Duplicado', 'extra_price':'0'}).status_code, 400)

    def test_edit_product_changes_new_offer_and_deactivates(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url('produto', self.product), {'version':edit_version(self.product), 'name':'Piloto novo', 'kind':'PILOTO', 'description':'Kit atualizado', 'includes_kit':'on'})
        self.assertEqual(response.status_code,302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.active)
        self.assertFalse(self.product.includes_shirt)

    def test_coupon_create_and_edit(self):
        self.client.force_login(self.user)
        data = dict(code_upper='cupomteste', kind='FIXED', amount='5,50', percent_off=10, max_uses=20, active='on', valid_from='2026-01-01T00:00', valid_until='2027-01-01T00:00')
        self.assertEqual(self.client.post(self.url('cupom'), data).status_code,302)
        coupon = Coupon.objects.get(event=self.event)
        self.assertEqual((coupon.code_upper,coupon.value_cents),('CUPOMTESTE',550))
        data.update(version=edit_version(coupon), amount='7,25')
        self.assertEqual(self.client.post(self.url('cupom',coupon),data).status_code,302)
        coupon.refresh_from_db(); self.assertEqual(coupon.value_cents,725)

    def test_permissions_scope_and_malformed_ids(self):
        operator = User.objects.create_user(email='catalog-denied@example.test')
        EventMembership.objects.create(user=operator, event=self.event, role_code=RoleChoices.CHECKIN_AGENT)
        self.client.force_login(operator)
        self.assertEqual(self.client.post(self.url('lote', self.lot), self.lot_data()).status_code,403)
        self.client.force_login(self.user)
        other = Event.objects.create(organization=self.org, title='Other', slug='catalog-other', starts_at=self.event.starts_at, ends_at=self.event.ends_at, registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        self.assertEqual(self.client.get(self.url('lote',self.lot,event=other)).status_code,404)
        self.assertEqual(self.client.get(self.url('lote')+'?product=not-an-id').status_code,404)

    def test_forms_render_and_catalog_has_edit_actions(self):
        self.client.force_login(self.user)
        for kind, item in [('produto',self.product), ('lote',self.lot)]:
            self.assertContains(self.client.get(self.url(kind,item)), 'Salvar alterações')
        self.assertContains(self.client.get(reverse('gestao:catalog',kwargs={'event_id':self.event.pk})), 'Editar lote')
