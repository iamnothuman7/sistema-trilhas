from django.test import TestCase, override_settings

from apps.core.models import AuditLog
from apps.identity.models import User


@override_settings(PUBLIC_SITE_URL="https://equipemorcego.com")
class AccessRecoveryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(email="dono@example.test", password="Example-test-2026", full_name="Dono")
        self.member = User.objects.create_user(email="piloto@example.test", password="Example-test-2026", full_name="Piloto", phone="(88) 99999-0000")

    def test_owner_generates_working_link_and_audit(self):
        self.client.force_login(self.owner)
        response = self.client.get("/gestao/acessos/?q=piloto")
        self.assertContains(response, "piloto@example.test")
        response = self.client.post("/gestao/acessos/", {"q": "piloto", "user_id": self.member.pk})
        link = response.context["generated"]["link"]
        self.assertTrue(link.startswith("https://equipemorcego.com/recuperar-senha/"))
        self.assertContains(response, "wa.me/5588999990000")
        self.assertTrue(AuditLog.objects.filter(action="ACCESS_LINK_GENERATED", entity_id=str(self.member.pk)).exists())
        self.client.logout()
        follow = self.client.get(link.replace("https://equipemorcego.com", ""), follow=True)
        self.assertContains(follow, "Defina sua senha")

    def test_staff_without_superuser_is_denied(self):
        staff = User.objects.create_user(email="equipe@example.test", password="Example-test-2026", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/gestao/acessos/").status_code, 403)
        self.assertEqual(self.client.post("/gestao/acessos/", {"user_id": self.member.pk}).status_code, 403)

    def test_owner_cannot_generate_link_for_self(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post("/gestao/acessos/", {"user_id": self.owner.pk}).status_code, 403)
