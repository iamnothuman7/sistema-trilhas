from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.identity.models import User, Person, UserPersonLink


@override_settings(ALLOWED_HOSTS=["testserver"])
class AuthenticationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.email = "piloto@example.test"
        cls.password = "test-only-account-password"
        User.objects.create_user(
            email=cls.email,
            password=cls.password,
            full_name="Piloto de Teste",
        )

    def login(self, next_url, *, secure=False):
        return Client().post(
            reverse("identity:login"),
            {"email": self.email, "password": self.password, "next": next_url},
            secure=secure,
        )

    def test_login_respects_relative_destination_with_query(self):
        response = self.login("/minha-conta/?origem=credencial")
        self.assertRedirects(
            response, "/minha-conta/?origem=credencial", fetch_redirect_response=False
        )

    def test_login_rejects_external_redirect_variants(self):
        for destination in (
            "https://external.example/",
            "//external.example/",
            "https://testserver@external.example/",
            "javascript:alert(1)",
            "\\\\external.example/",
        ):
            with self.subTest(destination=destination):
                response = self.login(destination)
                self.assertRedirects(
                    response,
                    reverse("minha_conta:dashboard"),
                    fetch_redirect_response=False,
                )

    def test_https_login_rejects_http_downgrade(self):
        response = self.login("http://testserver/minha-conta/", secure=True)
        self.assertRedirects(
            response,
            reverse("minha_conta:dashboard"),
            fetch_redirect_response=False,
        )

    def test_login_allows_same_origin_absolute_destination(self):
        response = self.login("https://testserver/minha-conta/", secure=True)
        self.assertRedirects(
            response,
            "https://testserver/minha-conta/",
            fetch_redirect_response=False,
        )

    def test_query_destination_survives_login_form_and_submission(self):
        url = reverse("identity:login") + "?next=/minha-conta/"
        response = self.client.get(url)
        self.assertContains(response, 'name="next" value="/minha-conta/"')
        response = self.client.post(
            url, {"email": self.email, "password": self.password}
        )
        self.assertRedirects(
            response, "/minha-conta/", fetch_redirect_response=False
        )

    def test_invalid_login_keeps_identifier_but_not_password(self):
        response = self.client.post(
            reverse("identity:login"),
            {"email": self.email, "password": "wrong-password-not-to-render"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="piloto@example.test"')
        self.assertContains(response, "Credenciais não conferem")
        self.assertNotContains(response, "wrong-password-not-to-render")

    def test_login_post_requires_csrf(self):
        response = Client(enforce_csrf_checks=True).post(
            reverse("identity:login"),
            {"email": self.email, "password": self.password},
        )
        self.assertEqual(response.status_code, 403)

    def test_registration_error_preserves_nonsecret_fields(self):
        response = self.client.post(
            reverse("identity:register"),
            {
                "full_name": "Nova Pilota",
                "email": "nova@example.test",
                "phone": "88900000000",
                "city": "Fortaleza",
                "state": "CE",
                "password": "first-password-not-to-render",
                "confirm_password": "different-password-not-to-render",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Nova Pilota"')
        self.assertContains(response, 'value="Fortaleza"')
        self.assertContains(response, "As senhas digitadas não coincidem")
        self.assertNotContains(response, "first-password-not-to-render")
        self.assertNotContains(response, "different-password-not-to-render")
        self.assertFalse(User.objects.filter(email="nova@example.test").exists())

    def test_registration_cannot_claim_existing_person_by_email(self):
        old_person = Person.objects.create(full_name="Outro piloto", email="nova@example.test")
        response = self.client.post(reverse("identity:register"), {
            "full_name": "Nova Conta", "email": "nova@example.test", "password": "local-test-pass",
            "confirm_password": "local-test-pass", "next": "/minha-conta/reivindicar/test-token/",
        })
        self.assertRedirects(response, "/minha-conta/reivindicar/test-token/", fetch_redirect_response=False)
        user = User.objects.get(email="nova@example.test")
        self.assertFalse(UserPersonLink.objects.filter(user=user, person=old_person).exists())
        self.assertEqual(user.person_links.count(), 1)

    def test_registration_rejects_external_next(self):
        response = self.client.post(reverse("identity:register"), {
            "full_name": "Nova Conta", "email": "nova@example.test", "password": "local-test-pass",
            "confirm_password": "local-test-pass", "next": "https://external.example/",
        })
        self.assertRedirects(response, reverse("minha_conta:dashboard"), fetch_redirect_response=False)

    def test_registration_rejects_invalid_email_and_short_password(self):
        for email, password in (("invalid-email", "valid-local-password"), ("nova@example.test", "short7")):
            response = self.client.post(reverse("identity:register"), {
                "full_name": "Nova Conta", "email": email, "password": password, "confirm_password": password,
            })
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.filter(email=email).exists())
