from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.core.management.base import CommandError
from django.db import close_old_connections, OperationalError
from django.db.models.query import QuerySet
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.management.commands.seed_initial_data import Command
from apps.core.models import RateLimitBucket
from apps.core.rate_limits import client_ip, consume_rate_limit, reset_rate_limit
from apps.identity.models import User, EventMembership
from apps.payments.services import reconcile_payment
from tests import test_payments as payment_fixtures
from tests.test_payments import MP_TEST_SETTINGS


AUTH_MIDDLEWARE = "apps.identity.middleware.AuthenticationRateLimitMiddleware"
TEST_MIDDLEWARE = [item for item in settings.MIDDLEWARE if item != AUTH_MIDDLEWARE] + [AUTH_MIDDLEWARE]


class PersistentRateLimitTests(TestCase):
    def test_failed_reset_preserves_counter_and_does_not_raise(self):
        consume_rate_limit("test", "buyer", limit=1, window_seconds=30)
        with patch("apps.core.rate_limits.RateLimitBucket.objects.filter") as query:
            query.return_value.update.side_effect = OperationalError("database is locked")
            self.assertFalse(reset_rate_limit("test", "buyer"))
        self.assertEqual(RateLimitBucket.objects.get().count, 1)

    def test_multiple_slots_are_charged_atomically_and_invalid_costs_rejected(self):
        self.assertTrue(consume_rate_limit("group", "buyer", limit=5, window_seconds=30, cost=3).allowed)
        self.assertFalse(consume_rate_limit("group", "buyer", limit=5, window_seconds=30, cost=3).allowed)
        self.assertEqual(RateLimitBucket.objects.get().count, 3)
        self.assertTrue(consume_rate_limit("group", "buyer", limit=5, window_seconds=30, cost=2).allowed)
        self.assertEqual(RateLimitBucket.objects.get().count, 5)
        for cost in (0, -1, 6, 1.2, True):
            with self.assertRaises(ValueError):
                consume_rate_limit("group", "buyer", limit=5, window_seconds=30, cost=cost)

    def test_limit_expiry_reset_and_private_keys(self):
        start = timezone.now()
        with patch("apps.core.rate_limits.timezone.now", return_value=start):
            self.assertTrue(consume_rate_limit("test", "private@example.test", limit=2, window_seconds=30).allowed)
            self.assertTrue(consume_rate_limit("test", "private@example.test", limit=2, window_seconds=30).allowed)
            rejected = consume_rate_limit("test", "private@example.test", limit=2, window_seconds=30)
            self.assertFalse(rejected.allowed)
            self.assertEqual(rejected.retry_after, 30)
        bucket = RateLimitBucket.objects.get()
        self.assertEqual(len(bucket.key), 64)
        self.assertNotIn("private", bucket.key)
        self.assertEqual(bucket.count, 2)
        with patch("apps.core.rate_limits.timezone.now", return_value=start + timedelta(seconds=31)):
            self.assertTrue(consume_rate_limit("test", "private@example.test", limit=2, window_seconds=30).allowed)
        reset_rate_limit("test", "private@example.test")
        bucket.refresh_from_db()
        self.assertEqual(bucket.count, 0)

    def test_forwarded_headers_cannot_select_remote_client_bucket(self):
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.7", HTTP_X_REAL_IP="198.51.100.9", HTTP_X_FORWARDED_FOR="198.51.100.10")
        self.assertEqual(client_ip(request), "198.51.100.7")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        self.assertEqual(client_ip(request), "198.51.100.9")
        request.META["HTTP_X_REAL_IP"] = "invalid, header"
        self.assertEqual(client_ip(request), "127.0.0.1")


class ConcurrentRateLimitTests(TransactionTestCase):
    def test_independent_database_connections_cannot_overspend(self):
        def spend(_):
            close_old_connections()
            try:
                return consume_rate_limit("parallel", "same-person", limit=6, window_seconds=60, cost=2).allowed
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as pool:
            accepted = sum(pool.map(spend, range(16)))
        self.assertGreater(accepted, 0)
        self.assertLessEqual(accepted, 3)
        self.assertEqual(RateLimitBucket.objects.get().count, accepted * 2)
        for _ in range(3 - accepted):
            self.assertTrue(consume_rate_limit("parallel", "same-person", limit=6, window_seconds=60, cost=2).allowed)
        self.assertFalse(consume_rate_limit("parallel", "same-person", limit=6, window_seconds=60, cost=2).allowed)


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE)
class AuthenticationHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Trilhas-fortes-4962"
        cls.owner = User.objects.create_superuser(email="owner@example.test", password=cls.password, full_name="Responsável")

    def post_login(self, identifier, password="incorrect", *, admin=False, ip="198.51.100.8"):
        return Client().post(reverse("admin:login" if admin else "identity:login"),
                             {"username" if admin else "email": identifier, "password": password}, REMOTE_ADDR=ip)

    def test_public_admin_and_aliases_share_account_budget_across_ip_addresses(self):
        for index in range(10):
            response = self.post_login(("admin", "morcego", "kelvim", self.owner.email.upper())[index % 4], ip=f"198.51.100.{index+1}")
            self.assertEqual(response.status_code, 200)
        response = self.post_login(self.owner.email, self.password, admin=True, ip="203.0.113.8")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertNotContains(response, self.password, status_code=429)
        self.assertNotIn("_auth_user_id", response.wsgi_request.session)

    def test_success_resets_account_budget_and_existing_password_remains_valid(self):
        for _ in range(9):
            self.assertEqual(self.post_login("admin").status_code, 200)
        self.assertEqual(self.post_login("admin", self.password).status_code, 302)
        for _ in range(9):
            self.assertEqual(self.post_login(self.owner.email).status_code, 200)
        self.assertEqual(self.post_login(self.owner.email, self.password, admin=True).status_code, 302)

    def test_logged_in_redirect_cannot_reset_another_account_budget(self):
        victim = User.objects.create_user(email="victim@example.test", password="Outra-senha-segura-3927", full_name="Piloto")
        for _ in range(9):
            self.assertEqual(self.post_login(victim.email).status_code, 200)
        browser = Client()
        browser.force_login(self.owner)
        response = browser.post(reverse("identity:login"), {"email": victim.email, "password": "incorrect"}, REMOTE_ADDR="203.0.113.2")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.post_login(victim.email, ip="203.0.113.3").status_code, 429)

    def test_admin_unicode_normalization_cannot_bypass_account_budget(self):
        full_width = "".join(chr(ord(char) + 0xFEE0) for char in self.owner.email)
        for index in range(10):
            value = full_width if index % 2 else self.owner.email
            self.assertEqual(self.post_login(value, admin=True, ip=f"198.51.100.{index+1}").status_code, 200)
        self.assertEqual(self.post_login(full_width, self.password, admin=True, ip="203.0.113.9").status_code, 429)

    def test_optional_counter_reset_failure_does_not_break_successful_login(self):
        original_update = QuerySet.update

        def update(queryset, **kwargs):
            if queryset.model is RateLimitBucket and kwargs == {"count": 0}:
                raise OperationalError("database is locked")
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", update):
            response = self.post_login(self.owner.email, self.password)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.wsgi_request.session["_auth_user_id"], str(self.owner.pk))
        self.assertEqual(list(RateLimitBucket.objects.values_list("count", flat=True)), [1, 1])

    def test_ip_bucket_stops_distributed_identifiers_and_xff_bypass(self):
        for index in range(40):
            self.assertEqual(self.post_login(f"unknown-{index}@example.test").status_code, 200)
        response = Client().post(reverse("identity:login"), {"email": "different@example.test", "password": "guess"},
                                 REMOTE_ADDR="198.51.100.8", HTTP_X_FORWARDED_FOR="203.0.113.1", HTTP_X_REAL_IP="203.0.113.2")
        self.assertEqual(response.status_code, 429)

    def test_signup_rejects_common_numeric_and_similar_passwords(self):
        for password in ("12345678", "password", "pilot@example.test"):
            response = self.client.post(reverse("identity:register"), {
                "email": "pilot@example.test", "full_name": "Piloto", "password": password, "confirm_password": password,
            })
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.filter(email="pilot@example.test").exists())
        response = self.client.post(reverse("identity:register"), {
            "email": "pilot@example.test", "full_name": "Piloto", "password": self.password, "confirm_password": self.password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(email="pilot@example.test").check_password(self.password))

    def test_signup_limit_keeps_form_and_password_private(self):
        data = {"email": "pilot@example.test", "full_name": "Piloto", "password": "mismatched-secret", "confirm_password": "other"}
        for _ in range(5):
            self.assertEqual(self.client.post(reverse("identity:register"), data).status_code, 200)
        response = self.client.post(reverse("identity:register"), data)
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Muitas tentativas", status_code=429)
        self.assertNotContains(response, "mismatched-secret", status_code=429)
        self.assertFalse(User.objects.filter(email="pilot@example.test").exists())


class AdminSeedHardeningTests(TestCase):
    def test_admin_does_not_offer_nonfunctional_second_factor_flag(self):
        user_admin = admin.site._registry[User]
        field_names = [name for _, options in user_admin.fieldsets for name in options["fields"]]
        self.assertNotIn("mfa_enabled", field_names)
        self.assertNotIn("mfa_enabled", user_admin.list_filter)
        description = " ".join(options.get("description", "") for _, options in user_admin.fieldsets)
        self.assertIn("Segundo fator ainda não configurado", description)

    def command(self):
        output = StringIO()
        return Command(stdout=output), output

    def test_existing_account_password_flags_and_roles_are_never_overwritten(self):
        user = User.objects.create_user(email="kelvim@equipemorcego.com.br", full_name="Nome atual", password="Senha-preservada-9284")
        original = user.password
        command, output = self.command()
        with patch.dict("os.environ", {"MORCEGO_INITIAL_ADMIN_PASSWORD": "untrusted-supplied-password"}):
            self.assertEqual(command.ensure_admin_user().pk, user.pk)
        user.refresh_from_db()
        self.assertEqual(user.password, original)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.full_name, "Nome atual")
        self.assertFalse(EventMembership.objects.filter(user=user).exists())
        self.assertNotIn("untrusted-supplied-password", output.getvalue())

    def test_creation_requires_environment_password_and_validates_it(self):
        command, output = self.command()
        for password in ("", "12345678"):
            with patch.dict("os.environ", {"MORCEGO_INITIAL_ADMIN_PASSWORD": password}), self.assertRaises(CommandError):
                command.ensure_admin_user()
        self.assertFalse(User.objects.exists())
        password = "Pedra-serra-caminho-4825"
        with patch.dict("os.environ", {"MORCEGO_INITIAL_ADMIN_PASSWORD": password}):
            user = command.ensure_admin_user()
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password(password))
        self.assertTrue(EventMembership.objects.filter(user=user, event__isnull=True, role_code="OWNER").exists())
        self.assertNotIn(password, output.getvalue())


@override_settings(**MP_TEST_SETTINGS)
class PurchaseAccessRateLimitTests(TestCase):
    setUp = payment_fixtures.PaymentReconciliationTests.setUp
    payment = payment_fixtures.PaymentReconciliationTests.payment

    def test_existing_password_attempts_are_limited_then_available_after_expiry(self):
        password = "Conta-trilha-segura-4829"
        user = User.objects.create_user(email=self.order.purchaser_email, full_name="Comprador", password=password)
        reconcile_payment(self.payment())
        url = reverse("identity:purchase_access", args=[self.order.pk])
        with patch("apps.identity.views_checkout.authenticate", return_value=None):
            for _ in range(10):
                self.assertEqual(self.client.post(url, {"password": "wrong"}).status_code, 400)
            response = self.client.post(url, {"password": password})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertNotIn("_auth_user_id", self.client.session)
        with patch("apps.core.rate_limits.timezone.now", return_value=timezone.now() + timedelta(minutes=16)):
            response = self.client.post(url, {"password": password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))

    def test_first_password_can_be_corrected_without_losing_purchase_access(self):
        reconcile_payment(self.payment())
        url = reverse("identity:purchase_access", args=[self.order.pk])
        for _ in range(3):
            self.assertEqual(self.client.post(url, {"new_password1": "12345678", "new_password2": "12345678"}).status_code, 400)
        password = "Primeiro-acesso-seguro-5832"
        response = self.client.post(url, {"new_password1": password, "new_password2": password})
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertTrue(self.order.purchaser_user.check_password(password))
