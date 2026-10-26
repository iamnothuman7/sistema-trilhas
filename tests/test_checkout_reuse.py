from django.test import TestCase
from django.urls import reverse
from apps.checkout.models import Order
from apps.registrations.models import Registration
from tests import test_system as _fixtures
from tests import test_registration_offers as _offers


class CheckoutReuseTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp
    post_data = _offers.RegistrationOfferTests.post_data
    checkout_url = _offers.RegistrationOfferTests.checkout_url

    def test_default_is_self_registration_without_retyping_identity(self):
        response = self.client.get(self.checkout_url())
        self.assertTrue(response.context["buyer_is_participant"])
        data = self.post_data(buyer_is_participant="on", **{
            "participant_name[]": [""], "participant_document[]": [""],
            "participant_email[]": [""], "participant_phone[]": [""]})
        response = self.client.post(self.checkout_url(), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        reg = Registration.objects.get()
        self.assertEqual(reg.participant_cpf, data["purchaser_document"])
        self.assertEqual(reg.person.full_name, data["purchaser_name"])
        self.assertEqual(reg.person.email, order.purchaser_email)

    def test_unchecked_keeps_other_participants_identity(self):
        data = self.post_data()
        self.assertEqual(self.client.post(self.checkout_url(), data).status_code, 302)
        reg = Registration.objects.get()
        self.assertEqual(reg.participant_cpf, "52998224725")
        self.assertEqual(reg.person.full_name, "Piloto da edição")

    def test_group_reuses_only_first_participant_and_checks_duplicate_cpfs(self):
        data = self.post_data(buyer_is_participant="on", **{
            "participant_name[]": ["ignored", "Segundo piloto"],
            "participant_document[]": ["ignored", "52998224725"],
            "participant_product[]": [str(self.product.pk)] * 2,
            "participant_lot[]": [str(self.lot.pk)] * 2,
            "participant_shirt[]": ["G"] * 2})
        self.assertEqual(self.client.post(self.checkout_url(), data).status_code, 302)
        self.assertEqual(set(Registration.objects.values_list("participant_cpf", flat=True)), {"00000000191", "52998224725"})

    def test_invalid_buyer_cpf_is_rejected_when_reused_and_preserves_selection(self):
        response = self.client.post(self.checkout_url(), self.post_data(buyer_is_participant="on", purchaser_document="11111111111"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertTrue(response.context["buyer_is_participant"])
        self.assertContains(response, "CPF")
