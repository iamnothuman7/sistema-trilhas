from django.test import TestCase, Client
from django.utils import timezone
from apps.identity.models import User, EventMembership, RoleChoices
from apps.core.permissions import has_event_permission, get_user_event_permissions, can_manage_event
from tests import test_system as _fixtures

class GranularTeamPermissionsTests(TestCase):
    setUp = _fixtures.SistemaTrilhasTests.setUp

    def test_collaborator_with_view_and_print_only(self):
        """Testa o caso exato do áudio: colaborador que pode visualizar e imprimir, mas não pode editar nem excluir."""
        collaborator = User.objects.create_user(email="vendedor@trilhas.test", full_name="Vendedor Balcão", password="pass123User!")
        perms = {
            "dashboard": ["view"],
            "registrations": ["view", "print"],
            "checkin": [],
            "catalog": [],
            "addons": [],
            "shirts": [],
            "raffle": [],
            "rankings": [],
            "finance": [],
            "frames": [],
        }
        EventMembership.objects.create(
            user=collaborator,
            event=self.event,
            role_code=RoleChoices.CUSTOM,
            permissions_json=perms,
            granted_by=self.user
        )

        # Verifica helpers
        self.assertTrue(has_event_permission(collaborator, self.event, "registrations", "view"))
        self.assertTrue(has_event_permission(collaborator, self.event, "registrations", "print"))
        self.assertFalse(has_event_permission(collaborator, self.event, "registrations", "create"))
        self.assertFalse(has_event_permission(collaborator, self.event, "registrations", "edit"))
        self.assertFalse(has_event_permission(collaborator, self.event, "registrations", "cancel"))
        self.assertFalse(has_event_permission(collaborator, self.event, "finance", "view"))

        # Login com o colaborador
        self.client.force_login(collaborator)

        # Consegue acessar lista de inscrições
        res = self.client.get(f"/gestao/eventos/{self.event.id}/inscricoes/", secure=True)
        self.assertEqual(res.status_code, 200)
        # Permissões no contexto para botões
        self.assertFalse(res.context["user_perms"]["registrations"]["create"])
        self.assertFalse(res.context["user_perms"]["registrations"]["edit"])
        self.assertFalse(res.context["user_perms"]["registrations"]["cancel"])
        self.assertTrue(res.context["user_perms"]["registrations"]["view"])
        self.assertTrue(res.context["user_perms"]["registrations"]["print"])

        # Tentar abrir tela de inscrição manual bloqueia com 403
        res_create = self.client.get(f"/gestao/eventos/{self.event.id}/inscricoes/manual/", secure=True)
        self.assertEqual(res_create.status_code, 403)

        # Tentar acessar financeiro bloqueia com 403
        res_fin = self.client.get(f"/gestao/eventos/{self.event.id}/financeiro/", secure=True)
        self.assertEqual(res_fin.status_code, 403)

    def test_save_permissions_via_team_management_form(self):
        """Testa o salvamento pelo formulário interativo de permissões."""
        self.client.force_login(self.user)
        post_data = {
            "full_name": "Operador Novo",
            "email": "operador.novo@trilhas.test",
            "password": "SenhaForte123!",
            "role_preset": "CUSTOM",
            "perm_registrations_view": "1",
            "perm_registrations_create": "1",
            "perm_checkin_scan": "1",
            "perm_checkin_checkin": "1",
        }
        res = self.client.post(f"/gestao/eventos/{self.event.id}/equipe/", post_data, secure=True)
        self.assertIn(res.status_code, (200, 302))

        new_user = User.objects.get(email="operador.novo@trilhas.test")
        self.assertEqual(new_user.full_name, "Operador Novo")
        self.assertTrue(new_user.check_password("SenhaForte123!"))

        membership = EventMembership.objects.get(user=new_user, event=self.event, revoked_at__isnull=True)
        self.assertIn("view", membership.permissions_json.get("registrations", []))
        self.assertIn("create", membership.permissions_json.get("registrations", []))
        self.assertNotIn("edit", membership.permissions_json.get("registrations", []))
        self.assertIn("scan", membership.permissions_json.get("checkin", []))
        self.assertIn("checkin", membership.permissions_json.get("checkin", []))
        self.assertNotIn("deliver_kit", membership.permissions_json.get("checkin", []))

    def test_admin_total_preset_grants_all(self):
        """Testa se selecionar ADMIN libera todas as abas e ações."""
        self.client.force_login(self.user)
        post_data = {
            "full_name": "Gerente Geral",
            "email": "gerente@trilhas.test",
            "password": "SenhaForte123!",
            "role_preset": "ADMIN",
        }
        res = self.client.post(f"/gestao/eventos/{self.event.id}/equipe/", post_data, secure=True)
        self.assertIn(res.status_code, (200, 302))

        user = User.objects.get(email="gerente@trilhas.test")
        membership = EventMembership.objects.get(user=user, event=self.event, revoked_at__isnull=True)
        self.assertEqual(membership.role_code, RoleChoices.EVENT_MANAGER)
        self.assertTrue(has_event_permission(user, self.event, "finance", "view"))
        self.assertTrue(has_event_permission(user, self.event, "catalog", "create"))
        self.assertTrue(has_event_permission(user, self.event, "registrations", "cancel"))
