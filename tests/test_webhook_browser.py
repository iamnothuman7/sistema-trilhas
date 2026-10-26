from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.payments.models import ProviderEvent


class WebhookBrowserTests(TestCase):
    def test_get_and_head_explain_endpoint_without_contacting_gateway(self):
        url = reverse("payments:webhook")
        with patch("apps.payments.views.MercadoPagoGateway") as gateway:
            response = self.client.get(url + "?data.id=123&type=payment&token=not-for-display",
                HTTP_X_SIGNATURE="ts=123,v1=not-for-display", HTTP_X_REQUEST_ID="not-for-display")
            self.assertContains(response, "Endereço de notificações")
            self.assertContains(response, "não confirma nenhum pagamento")
            self.assertContains(response, reverse("gestao:mercadopago_connection"))
            self.assertNotContains(response, "not-for-display")
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
            head = self.client.head(url)
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b"")
            gateway.assert_not_called()
        self.assertFalse(ProviderEvent.objects.exists())

    def test_other_methods_still_rejected(self):
        with patch("apps.payments.views.MercadoPagoGateway") as gateway:
            for method in ("put", "patch", "delete"):
                self.assertEqual(getattr(self.client, method)(reverse("payments:webhook")).status_code, 405)
            gateway.assert_not_called()
