from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase

from apps.core.middleware import HtmxNavigationMiddleware


class HtmxNavigationTests(SimpleTestCase):
    def response(self, response, htmx=False):
        request = RequestFactory().get("/gestao/", **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
        return HtmxNavigationMiddleware(lambda request: response)(request)

    def test_successful_post_redirect_preserves_session_cookie_and_no_cache(self):
        original = HttpResponseRedirect("/gestao/patrocinadores/")
        original.set_cookie("sessionid", "test-session", httponly=True, secure=True)
        response = self.response(original, htmx=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Location"], "/gestao/patrocinadores/")
        self.assertNotIn("HX-Redirect", response)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.cookies["sessionid"].value, "test-session")
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertNotIn("Location", response)
        self.assertEqual(response.content, b"")

    def test_native_redirect_stays_native(self):
        response = self.response(HttpResponseRedirect("/login/"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")
        self.assertNotIn("HX-Redirect", response)
        self.assertIn("HX-Request", response["Vary"])

    def test_authentication_and_external_redirects_keep_native_navigation(self):
        for target in ['/login/?next=/gestao/', 'https://example.com/', '//example.com/gestao/']:
            response = self.response(HttpResponseRedirect(target), htmx=True)
            self.assertEqual(response['HX-Redirect'], target)
            self.assertNotIn('HX-Location', response)

    def test_validation_and_permission_failures_are_not_silenced(self):
        for status in (200, 400, 403, 404, 422, 500):
            with self.subTest(status=status):
                response = self.response(HttpResponse("form errors", status=status), htmx=True)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.content, b"form errors")
                self.assertIn("HX-Request", response["Vary"])
