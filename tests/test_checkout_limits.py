from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import CapacityHold
from apps.checkout.models import Order, OrderLine, ReceivableDue
from apps.core.rate_limits import consume_rate_limit
from apps.registrations.models import Registration
from tests import test_system as fixtures


class CheckoutLimitsTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp

    def data(self):
        return {'purchaser_name':'Comprador teste','purchaser_email':'limits@example.test',
                'purchaser_phone':'88999999999','purchaser_document':'52998224725',
                'participant_name[]':['Piloto de teste'], 'participant_document[]':['52998224725'],
                'participant_product[]':[str(self.product.pk)], 'participant_lot[]':[str(self.lot.pk)],
                'participant_shirt[]':['M']}

    def post(self, data):
        return self.client.post(reverse('checkout:start_registration', kwargs={'event_slug':self.event.slug}), data)

    def assert_no_reservation(self):
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderLine.objects.exists())
        self.assertFalse(Registration.objects.exists())
        self.assertFalse(ReceivableDue.objects.exists())
        self.assertFalse(CapacityHold.objects.exists())
        self.cap_resource.refresh_from_db()
        self.assertEqual(self.cap_resource.reserved_count, 0)

    def assert_full_seat_budget_available(self):
        self.assertTrue(consume_rate_limit('reservation-seats-ip', '127.0.0.1',
            limit=60, window_seconds=3600, cost=60).allowed)
        self.assertTrue(consume_rate_limit('reservation-seats-buyer', 'limits@example.test',
            limit=40, window_seconds=3600, cost=40).allowed)

    def test_group_size_limit_rejects_before_creating_a_reservation(self):
        data=self.data()
        data['participant_name[]']=['Piloto teste']*21
        before=Order.objects.count()
        response=self.post(data)
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'até 20 participantes',status_code=400)
        self.assertEqual(Order.objects.count(),before)

    def test_group_cost_spends_seats_and_denies_without_creating_order(self):
        consume_rate_limit('reservation-seats-buyer','limits@example.test',limit=40,window_seconds=3600,cost=39)
        data=self.data()
        data['participant_name[]'].append('Segundo piloto teste')
        data['participant_document[]'].append('11144477735')
        data['participant_product[]']*=2
        data['participant_lot[]']*=2
        data['participant_shirt[]']*=2
        before=Order.objects.count()
        response=self.post(data)
        self.assertEqual(response.status_code,429)
        self.assertGreater(int(response['Retry-After']),0)
        self.assertEqual(Order.objects.count(),before)

    def test_normal_purchase_still_creates_one_order(self):
        before=Order.objects.count()
        response=self.post(self.data())
        self.assertEqual(response.status_code,302)
        self.assertEqual(Order.objects.count(),before+1)
        self.assertTrue(consume_rate_limit('reservation-seats-ip', '127.0.0.1',
            limit=60, window_seconds=3600, cost=59).allowed)
        self.assertFalse(consume_rate_limit('reservation-seats-ip', '127.0.0.1',
            limit=60, window_seconds=3600).allowed)
        self.assertTrue(consume_rate_limit('reservation-seats-buyer', 'limits@example.test',
            limit=40, window_seconds=3600, cost=39).allowed)
        self.assertFalse(consume_rate_limit('reservation-seats-buyer', 'limits@example.test',
            limit=40, window_seconds=3600).allowed)

    def test_invalid_catalog_or_shirt_does_not_consume_seat_budget(self):
        for field, value in (('participant_product[]', ['invalid-product']),
                             ('participant_lot[]', ['invalid-lot']),
                             ('participant_shirt[]', ['INVALID'])):
            with self.subTest(field=field):
                data = self.data()
                data[field] = value
                self.assertEqual(self.post(data).status_code, 200)
        self.assert_no_reservation()
        self.assert_full_seat_budget_available()

    def test_duplicate_cpf_rejections_spend_no_additional_seats(self):
        self.assertEqual(self.post(self.data()).status_code, 302)
        for _ in range(3):
            self.assertContains(self.post(self.data()), 'Já existe uma inscrição ou reserva')
        self.assertEqual(Order.objects.count(), 1)
        self.assertTrue(consume_rate_limit('reservation-seats-ip', '127.0.0.1',
            limit=60, window_seconds=3600, cost=59).allowed)
        self.assertTrue(consume_rate_limit('reservation-seats-buyer', 'limits@example.test',
            limit=40, window_seconds=3600, cost=39).allowed)

    def test_ip_denial_does_not_spend_buyer_budget_or_leave_partial_reservation(self):
        consume_rate_limit('reservation-seats-ip', '127.0.0.1', limit=60, window_seconds=3600, cost=60)
        consume_rate_limit('reservation-seats-buyer', 'limits@example.test', limit=40, window_seconds=3600, cost=39)
        result = self.post(self.data())
        self.assertEqual(result.status_code, 429)
        self.assertGreater(int(result['Retry-After']), 0)
        self.assert_no_reservation()
        self.assertNotIn('checkout_order_ids', self.client.session)
        self.assertTrue(consume_rate_limit('reservation-seats-buyer', 'limits@example.test',
            limit=40, window_seconds=3600).allowed)
        # Submission attempts remain counted even though reservation writes roll back.
        self.assertFalse(consume_rate_limit('registration-submit:account', 'limits@example.test',
            limit=10, window_seconds=900, cost=10).allowed)

    def test_buyer_denial_rolls_back_ip_budget_and_entire_reservation(self):
        consume_rate_limit('reservation-seats-buyer', 'limits@example.test', limit=40, window_seconds=3600, cost=40)
        self.assertEqual(self.post(self.data()).status_code, 429)
        self.assert_no_reservation()
        self.assertTrue(consume_rate_limit('reservation-seats-ip', '127.0.0.1',
            limit=60, window_seconds=3600, cost=60).allowed)

    def test_partial_group_capacity_failure_rolls_back_without_spending_seats(self):
        self.lot.max_quantity = 1
        self.lot.save(update_fields=['max_quantity'])
        data = self.data()
        data['participant_name[]'].append('Segundo piloto teste')
        data['participant_document[]'].append('11144477735')
        for key in ('participant_product[]', 'participant_lot[]', 'participant_shirt[]'):
            data[key] *= 2
        self.assertContains(self.post(data), 'não tem vagas suficientes')
        self.assert_no_reservation()
        self.assert_full_seat_budget_available()
