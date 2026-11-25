"""Event-scoped team accounts and granular operational permissions."""
import json
import secrets
import string
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from apps.core.permissions import (
    management_required, is_administrator, MANAGERS,
    TAB_DEFINITIONS, ROLE_PRESETS
)
from apps.core.utils import log_audit
from .models import User, EventMembership, RoleChoices

_SCRIPT_UNSAFE = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def _script_json(value):
    """JSON seguro dentro de <script>: nomes digitados por usuários não fecham a tag."""
    return json.dumps(value).translate(_SCRIPT_UNSAFE)


def protected_user(actor, user):
    return (user.pk == actor.pk or user.is_staff or user.is_superuser or (not is_administrator(actor) and
        user.event_memberships.filter(role_code__in=MANAGERS, revoked_at__isnull=True).exists()))


@management_required
@never_cache
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def team_management(request, event_id):
    event = request.management_event
    new_access = None
    form_errors = []

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '').strip()
        role_preset = request.POST.get('role_preset', 'CUSTOM').strip()
        member_id = request.POST.get('member_id', '').strip()

        if not full_name:
            form_errors.append("O Nome Completo é obrigatório.")
        if not email or "@" not in email:
            form_errors.append("Informe um e-mail de acesso válido.")

        # Build permissions_json from submitted checkboxes
        permissions_json = {}
        for tab in TAB_DEFINITIONS:
            tab_id = tab["id"]
            actions = []
            for act_id, _, _ in tab["actions"]:
                if request.POST.get(f"perm_{tab_id}_{act_id}") in ("1", "on", "true", True):
                    actions.append(act_id)
            permissions_json[tab_id] = actions

        # Preset role mapping
        preset_info = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["CUSTOM"])
        role_code = preset_info.get("role_code", RoleChoices.CUSTOM)

        if role_preset == "ADMIN":
            role_code = RoleChoices.EVENT_MANAGER
            permissions_json = {tab["id"]: [act[0] for act in tab["actions"]] for tab in TAB_DEFINITIONS}

        if not form_errors:
            with transaction.atomic():
                user = User.objects.select_for_update().filter(email=email).first()
                if user and protected_user(request.user, user) and user.pk != request.user.pk:
                    form_errors.append("Esta conta tem acesso administrativo protegido e não pode ser editada por aqui.")
                elif user and not user.is_active:
                    form_errors.append("Esta conta está desativada no sistema. Contate a administração geral.")
                elif user and password and not request.user.is_superuser and user.pk != request.user.pk:
                    # Gerente de evento não troca senha de conta existente: seria tomar a conta de outra pessoa.
                    form_errors.append("Esta conta já existe. Deixe a senha em branco para só dar o acesso; para trocar a senha, o administrador geral gera um link em Recuperar acesso.")
                else:
                    if user is None:
                        generated_pass = password or ('Trilha' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8)))
                        user = User.objects.create_user(email=email, full_name=full_name, password=generated_pass)
                        new_access = {'email': user.email, 'password': generated_pass}
                    else:
                        if password:
                            user.set_password(password)
                            user.save(update_fields=['password'])
                        if full_name and (is_administrator(request.user) or not user.full_name):
                            user.full_name = full_name
                            user.save(update_fields=['full_name'])

                    membership = EventMembership.objects.filter(user=user, event=event, revoked_at__isnull=True).first()
                    if membership:
                        membership.role_code = role_code
                        membership.permissions_json = permissions_json
                        membership.granted_by = request.user
                        membership.save(update_fields=['role_code', 'permissions_json', 'granted_by'])
                        action_logged = 'TEAM_ACCESS_EDITED'
                    else:
                        EventMembership.objects.create(
                            user=user,
                            event=event,
                            role_code=role_code,
                            permissions_json=permissions_json,
                            granted_by=request.user
                        )
                        action_logged = 'TEAM_ACCESS_GRANTED'

                    log_audit(
                        actor=request.user,
                        event_id=event.pk,
                        action=action_logged,
                        entity_type='User',
                        entity_id=user.pk,
                        diff={'role_code': role_code, 'permissions': permissions_json},
                        request=request
                    )
                    messages.success(request, f"Usuário e permissões salvos com sucesso para {user.full_name}!")
                    if not new_access:
                        return redirect('gestao:team_management', event_id=event.pk)

    memberships = EventMembership.objects.filter(event=event, revoked_at__isnull=True).select_related('user').order_by('created_at')
    members_data = []
    members_client_map = {}
    tab_name_map = {tab["id"]: tab["name"] for tab in TAB_DEFINITIONS}

    for m in memberships:
        p_json = m.permissions_json or {}
        granted_tab_names = []
        for tab in TAB_DEFINITIONS:
            t_id = tab["id"]
            if p_json.get(t_id):
                granted_tab_names.append(tab["name"])

        if not granted_tab_names:
            if m.role_code in MANAGERS:
                granted_tab_names = ["Todas as Abas (Admin Total)"]
            else:
                default_preset = next((k for k, v in ROLE_PRESETS.items() if v.get("role_code") == m.role_code), "CUSTOM")
                preset_perms = ROLE_PRESETS.get(default_preset, {}).get("permissions", {})
                for t_id, acts in preset_perms.items():
                    if acts and t_id in tab_name_map:
                        granted_tab_names.append(tab_name_map[t_id])

        m_dict = {
            "id": str(m.id),
            "user_id": str(m.user_id),
            "full_name": m.user.full_name,
            "email": m.user.email,
            "role_code": m.role_code,
            "role_label": m.get_role_code_display(),
            "role_preset": next((k for k, v in ROLE_PRESETS.items() if v.get("role_code") == m.role_code), "CUSTOM"),
            "permissions_json": p_json,
            "granted_tabs": granted_tab_names,
            "editable": not protected_user(request.user, m.user),
            "created_at": m.created_at.strftime("%d/%m/%Y"),
        }
        members_data.append(m_dict)
        members_client_map[str(m.user_id)] = m_dict

    selected_user_id = request.GET.get('edit_user', '')

    context = {
        'event': event,
        'members': members_data,
        'members_json': _script_json(members_client_map),
        'tab_definitions': TAB_DEFINITIONS,
        'role_presets': ROLE_PRESETS,
        'role_presets_json': _script_json({k: v["permissions"] for k, v in ROLE_PRESETS.items()}),
        'new_access': new_access,
        'form_errors': form_errors,
        'selected_user_id': selected_user_id,
    }
    return render(request, 'gestao/team/manage.html', context, status=400 if form_errors else 200)


@management_required
@never_cache
@require_http_methods(['GET'])
def team_edit(request, event_id, user_id):
    """Redireciona para o painel de equipe com o colaborador selecionado para edição rápida."""
    return redirect(f"{redirect('gestao:team_management', event_id=event_id).url}?edit_user={user_id}")


@management_required
@never_cache
@require_POST
def team_revoke(request, event_id, user_id):
    user = get_object_or_404(User, pk=user_id)
    if protected_user(request.user, user):
        raise PermissionDenied('Este acesso administrativo não pode ser removido por esta tela.')
    count = EventMembership.objects.filter(event=request.management_event, user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
    if count:
        log_audit(actor=request.user, event_id=event_id, action='TEAM_ACCESS_REVOKED', entity_type='User', entity_id=user.pk, request=request)
    messages.success(request, f'Acesso de {user.full_name} removido com sucesso deste evento.')
    return redirect('gestao:team_management', event_id=event_id)
