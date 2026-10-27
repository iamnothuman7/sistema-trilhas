from datetime import timedelta
from uuid import uuid4
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.catalog.models import Lot, CapacityResource, CapacityHold
from apps.checkout.models import Order, OrderLine
from apps.core.models import AuditLog
from apps.events.models import Event
from apps.identity.models import User, EventMembership, RoleChoices
from tests import test_system as fixtures


class LotRemovalTests(TestCase):
    def setUp(self):
        fixtures.SistemaTrilhasTests.setUp(self)
        self.client.force_login(self.user)
        self.url = reverse('gestao:lot_remove', args=[self.event.pk, self.lot.pk])
        self.resource = CapacityResource.objects.create(event=self.event, type='LOT_TOTAL', reference_id=str(self.lot.pk), capacity_total=50)

    def test_unused_lot_deletion_requires_explicit_post_and_cleans_capacity(self):
        self.assertContains(self.client.get(self.url), 'Excluir lote')
        self.assertTrue(Lot.objects.filter(pk=self.lot.pk).exists())
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.assertTrue(Lot.objects.filter(pk=self.lot.pk).exists())
        self.assertRedirects(self.client.post(self.url, {'confirm': 'yes'}), reverse('gestao:catalog', args=[self.event.pk]))
        self.assertFalse(Lot.objects.filter(pk=self.lot.pk).exists())
        self.assertFalse(CapacityResource.objects.filter(pk=self.resource.pk).exists())
        self.assertTrue(CapacityResource.objects.filter(pk=self.cap_resource.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action='LOT_DELETED', entity_id=str(self.lot.pk)).exists())

    def test_order_created_after_preview_is_preserved_and_lot_deactivated(self):
        self.assertContains(self.client.get(self.url), 'Excluir lote')
        order = Order.objects.create(event=self.event, merchant_reference='LOT-HISTORY', status='PAID', total_cents=18000, expires_at=timezone.now()+timedelta(hours=1))
        line = OrderLine.objects.create(order=order, product=self.product, lot=self.lot, net_cents=18000)
        self.assertEqual(self.client.post(self.url, {'confirm': 'yes'}).status_code, 302)
        self.lot.refresh_from_db(); line.refresh_from_db(); order.refresh_from_db()
        self.assertFalse(self.lot.active)
        self.assertEqual(line.lot_id, self.lot.pk)
        self.assertEqual((line.net_cents, order.status), (18000, 'PAID'))
        self.assertTrue(AuditLog.objects.filter(action='LOT_DEACTIVATED').exists())

    def test_reservations_counters_and_past_holds_prevent_hard_deletion(self):
        for condition in ('reserved', 'committed', 'sold', 'past_hold'):
            with self.subTest(condition=condition):
                self.lot.active = True; self.lot.current_sold = int(condition == 'sold'); self.lot.save()
                self.resource.reserved_count = int(condition == 'reserved')
                self.resource.committed_count = int(condition == 'committed'); self.resource.save()
                if condition == 'past_hold':
                    CapacityHold.objects.create(resource=self.resource, order_id=uuid4(), quantity=1, status='EXPIRED', expires_at=timezone.now(), idempotency_key='lot-history')
                self.assertContains(self.client.get(self.url), 'Desativar lote')
                self.assertEqual(self.client.post(self.url, {'confirm': 'yes'}).status_code, 302)
                self.lot.refresh_from_db(); self.assertFalse(self.lot.active)
                self.assertTrue(CapacityResource.objects.filter(pk=self.resource.pk).exists())

    def test_event_permission_csrf_and_cross_event_protection(self):
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url, {'confirm': 'yes'}).status_code, 403)
        other = Event.objects.create(organization=self.org, title='Outro', slug='other-lot-delete', starts_at=self.event.starts_at, ends_at=self.event.ends_at, registration_opens_at=self.event.registration_opens_at, registration_closes_at=self.event.registration_closes_at)
        self.assertEqual(self.client.post(reverse('gestao:lot_remove', args=[other.pk, self.lot.pk]), {'confirm': 'yes'}).status_code, 404)
        user = User.objects.create_user(email='cannot-delete-lot@example.test')
        EventMembership.objects.create(user=user, event=self.event, role_code=RoleChoices.FINANCE)
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.url, {'confirm': 'yes'}).status_code, 403)
        self.assertTrue(Lot.objects.filter(pk=self.lot.pk).exists())
