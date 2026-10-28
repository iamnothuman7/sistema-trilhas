"""Event-scoped team accounts and operational permissions."""
import secrets
import string
from django import forms
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from apps.core.permissions import management_required, is_administrator, MANAGERS
from apps.core.utils import log_audit
from .models import User, EventMembership, RoleChoices

OPERATIONAL_ROLES = [RoleChoices.REGISTRATION_AGENT, RoleChoices.CHECKIN_AGENT, RoleChoices.FINANCE, RoleChoices.CONTENT]


class TeamAccessForm(forms.Form):
    email = forms.EmailField(label="E-mail de acesso", max_length=254)
    full_name = forms.CharField(label="Nome completo", max_length=150)
    password = forms.CharField(label="Senha inicial (somente nova conta)", required=False,
        widget=forms.PasswordInput, help_text="Deixe em branco para gerar uma senha. Contas existentes mantêm a senha atual.")
    roles = forms.MultipleChoiceField(label="Permissões neste evento", widget=forms.CheckboxSelectMultiple)
    replace_permissions = forms.BooleanField(label="Substituir as permissões anteriores desta pessoa neste evento", required=False, initial=True)

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = OPERATIONAL_ROLES + ([RoleChoices.EVENT_MANAGER] if is_administrator(actor) else [])
        self.fields['roles'].choices = [(role, role.label) for role in allowed]
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        if password:
            validate_password(password, User(email=self.data.get('email', ''), full_name=self.data.get('full_name', '')))
        return password


def protected_user(actor, user):
    return (user.pk == actor.pk or user.is_staff or user.is_superuser or (not is_administrator(actor) and
        user.event_memberships.filter(role_code__in=MANAGERS, revoked_at__isnull=True).exists()))


@management_required
@never_cache
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def team_management(request, event_id):
    event = request.management_event
    form = TeamAccessForm(request.POST if request.method == 'POST' else None, actor=request.user)
    new_access = None
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = User.objects.select_for_update().filter(email=data['email']).first()
            if user and protected_user(request.user, user):
                form.add_error('email', 'Esta conta tem acesso administrativo protegido. Use outra conta para a equipe.')
            elif user and not user.is_active:
                form.add_error('email', 'Esta conta está desativada. A administração geral precisa revisar o acesso.')
            elif user and data['password']:
                form.add_error('password', 'Esta conta já existe. Deixe a senha em branco para manter o acesso atual.')
            else:
                if user is None:
                    password = data['password'] or 'Trilha' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(9))
                    user = User.objects.create_user(email=data['email'], full_name=data['full_name'], password=password)
                    new_access = {'email': user.email, 'password': password}
                for role in data['roles']:
                    EventMembership.objects.get_or_create(user=user, event=event, role_code=role,
                        revoked_at=None, defaults={'granted_by': request.user})
                if data.get('replace_permissions'):
                    EventMembership.objects.filter(user=user, event=event, revoked_at__isnull=True).exclude(
                        role_code__in=data['roles']).update(revoked_at=timezone.now())
                log_audit(actor=request.user, event_id=event.pk, action='TEAM_ACCESS_GRANTED', entity_type='User',
                    entity_id=user.pk, diff={'roles': data['roles']}, request=request)
                messages.success(request, 'Acesso salvo para este evento.')
                if not new_access:
                    return redirect('gestao:team_management', event_id=event.pk)
                form = TeamAccessForm(actor=request.user)
    memberships = EventMembership.objects.filter(event=event, revoked_at__isnull=True).select_related('user')
    members = {}
    for membership in memberships:
        item = members.setdefault(membership.user_id, {'user': membership.user, 'roles': [], 'editable': not protected_user(request.user, membership.user)})
        item['roles'].append(membership.get_role_code_display())
    return render(request, 'gestao/team/manage.html', {'event': event, 'form': form,
        'members': list(members.values()), 'new_access': new_access}, status=400 if form.errors else 200)


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
    messages.success(request, 'Acesso removido deste evento. A conta continua disponível para seus outros vínculos.')
    return redirect('gestao:team_management', event_id=event_id)
