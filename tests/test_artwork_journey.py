"""Payment-gated artwork, first access continuation and independent exports."""
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.checkout.models import OrderStatus
from apps.mediaframes.access import purchased_art_url
from apps.mediaframes.events_config import get_event_versions
from tests import test_cpf_and_pilot_art as pilot_fixtures
from tests import test_checkout_account as account_fixtures
from tests import test_mediaframe_security as file_fixtures
from tests import test_payments as payment_fixtures
from tests import test_system as fixtures


class ArtworkJourneyTests(TestCase):
    setUp = fixtures.SistemaTrilhasTests.setUp
    pilot = pilot_fixtures.PaidPilotArtTests.pilot

    def test_public_cta_is_only_present_for_paid_pilot_of_this_event(self):
        detail = reverse("events:detail", args=[self.event.slug])
        self.assertNotContains(self.client.get(detail), "Criar story e feed")
        _, order, _ = self.pilot(status=OrderStatus.AWAITING_PAYMENT)
        self.assertNotContains(self.client.get(detail), "Criar story e feed")
        order.status = OrderStatus.PAID
        order.save()
        response = self.client.get(detail)
        self.assertContains(response, "Criar story e feed")
        self.assertIn("no-store", response["Cache-Control"])
        order.status = OrderStatus.REFUNDED
        order.save()
        self.assertNotContains(self.client.get(detail), "Criar story e feed")

    def test_paid_result_opens_editor_with_its_registration_and_pdf(self):
        user, order, reg = self.pilot()
        url = purchased_art_url(user, order)
        response = self.client.get(reverse("checkout:order_result", args=[order.pk]), follow=True)
        self.assertEqual(response.redirect_chain, [(url, 302)])
        self.assertContains(response, "Pagamento confirmado. Você está dentro!")
        self.assertContains(response, 'data-art-format="story"')
        self.assertContains(response, 'data-art-format="feed"')
        self.assertContains(response, reverse("minha_conta:registration_pdf", args=[reg.pk]))
        self.assertEqual(response.context["selected_registration"], reg)
        self.assertContains(response, reverse("minha_conta:registration_detail", args=[reg.pk]) + "#participante")
        self.assertContains(response, "Confirmar participante")

    def test_unpaid_or_companion_does_not_redirect_to_editor(self):
        _, order, _ = self.pilot(kind="ACOMPANHANTE")
        self.assertEqual(self.client.get(reverse("checkout:order_result", args=[order.pk])).status_code, 200)
        order.status = OrderStatus.AWAITING_PAYMENT
        order.save()
        response = self.client.get(reverse("checkout:order_result", args=[order.pk]))
        self.assertContains(response, "Pagamento ainda não confirmado")
        self.assertNotContains(response, 'id="story-canvas"')

    def test_foreign_registration_parameter_never_grants_access(self):
        _, _, reg = self.pilot()
        self.client.force_login(self.user)
        response = self.client.get(reverse("events:frames", args=[self.event.slug]), {"inscricao": reg.pk, "origem": "compra"})
        self.assertNotContains(response, 'id="story-canvas"')
        self.assertNotContains(response, "Pagamento confirmado. Você está dentro!")

    def test_feed_layout_fits_without_mutating_story(self):
        version = get_event_versions(self.event)[0]
        self.assertEqual(version["layout"]["footer_y"], 1680)
        self.assertLess(version["feed_layout"]["footer_y"] + 205, 1350)
        self.assertLess(version["feed_layout"]["logo"]["y"] + version["feed_layout"]["logo"]["height"],
                        version["feed_layout"]["photo"]["y"] - version["feed_layout"]["pop_out"])


@override_settings(**payment_fixtures.MP_TEST_SETTINGS)
class FirstAccessArtworkTests(TestCase):
    setUp = payment_fixtures.PaymentReconciliationTests.setUp
    payment = payment_fixtures.PaymentReconciliationTests.payment
    paid = account_fixtures.PurchaseAccountTests.paid

    def test_paid_pilot_sets_first_password_then_automatically_opens_editor(self):
        self.event.status = "PUBLISHED"
        self.event.save()
        user = self.paid()
        url = reverse("identity:purchase_access", args=[self.order.pk])
        # Knowing the authorized purchase does not skip setting a password.
        response = self.client.get(url)
        self.assertContains(response, "Sua conta já está criada")
        self.assertNotIn("_auth_user_id", self.client.session)
        password = "Compra-Teste-Segura-2027"
        response = self.client.post(url, {"new_password1": password, "new_password2": password}, follow=True)
        self.assertEqual(response.redirect_chain, [(purchased_art_url(user, self.order), 302)])
        self.assertContains(response, 'id="story-canvas"')
        self.assertContains(response, "Pagamento confirmado. Você está dentro!")


class ArtworkExportTests(TestCase):
    setUp = file_fixtures.MontageSecurityTests.setUp
    pilot = pilot_fixtures.PaidPilotArtTests.pilot
    png = file_fixtures.MontageSecurityTests.png
    send = file_fixtures.MontageSecurityTests.send

    def test_story_and_feed_downloads_have_independent_files(self):
        story = self.send(route="montage-download", format="story")
        feed = self.send(self.png("blue"), route="montage-download", format="feed")
        self.assertEqual((story.status_code, feed.status_code), (200, 200))
        self.montage.refresh_from_db()
        story_name = self.montage.generated_image.name
        with self.montage.generated_image.open("rb") as stored:
            self.assertEqual(stored.read(), story.content)
        with self.montage.feed_image.open("rb") as stored:
            self.assertEqual(stored.read(), feed.content)
        self.assertNotEqual(self.montage.sha256, self.montage.feed_sha256)
        self.assertEqual(self.montage.downloads_count, 2)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.send(self.png("green"), format="feed").status_code, 200)
        self.montage.refresh_from_db()
        self.assertEqual(self.montage.generated_image.name, story_name)
        self.assertTrue(self.montage.generated_image.storage.exists(story_name))

    def test_invalid_format_and_refund_cannot_create_feed(self):
        self.assertEqual(self.send(format="../feed").status_code, 400)
        order = self.registration.source_order_line.order
        order.status = OrderStatus.REFUNDED
        order.save()
        self.assertEqual(self.send(route="montage-download", format="feed").status_code, 403)
        self.montage.refresh_from_db()
        self.assertFalse(self.montage.feed_image)
