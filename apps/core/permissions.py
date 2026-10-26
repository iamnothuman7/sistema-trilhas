"""Explicit per-event capabilities, adapted from Navie's scoped access pattern."""
from functools import wraps
from uuid import UUID
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404
from apps.events.models import Event
from apps.identity.models import EventMembership, RoleChoices

MANAGERS = {RoleChoices.OWNER, RoleChoices.EVENT_MANAGER}
ACTION_ROLES = {
    "view": set(RoleChoices.values),
    "events": MANAGERS,
    "registrations": MANAGERS | {RoleChoices.REGISTRATION_AGENT},
    "checkin": MANAGERS | {RoleChoices.CHECKIN_AGENT, RoleChoices.REGISTRATION_AGENT},
    "finance": MANAGERS | {RoleChoices.FINANCE},
    "catalog": MANAGERS,
    "logistics": MANAGERS | {RoleChoices.LOGISTICS},
    "content": MANAGERS | {RoleChoices.CONTENT},
    "audit": MANAGERS,
}
VIEW_ACTIONS = {
    "catalog_editor": "catalog",
    "usage_guide": "view",
    "dashboard": "view", "event_list": "events", "event_create": "events",
    "event_detail": "events", "event_edit": "events", "event_duplicate": "events", "event_appearance": "content",
    "registration_list": "registrations", "registration_detail": "registrations",
    "registration_change_status": "registrations", "export_participants": "registrations",
    "credenciamento_scanner": "checkin", "credenciamento_lookup_api": "checkin",
    "credenciamento_confirm_api": "checkin", "deliver_benefit_api": "checkin",
    "catalog_management": "catalog", "finance_overview": "finance", "add_expense": "finance",
    "pay_expense": "finance", "cancel_expense": "finance", "export_financial": "finance",
    "event_reports": "finance", "expedition_management": "logistics", "frame_management": "content",
    "audit_log_view": "audit",
    "sponsor_list": "content", "sponsor_create": "content", "sponsor_edit": "content",
    "raffle_management": "events",
    "team_frame_editor": "content",
    "ranking_management": "registrations",
}


def is_administrator(user):
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def active_memberships(user, action="view"):
    if not user.is_authenticated or not user.is_active:
        return EventMembership.objects.none()
    return EventMembership.objects.filter(user=user, revoked_at__isnull=True, role_code__in=ACTION_ROLES.get(action, set()))


def accessible_events(user, action="view"):
    if is_administrator(user):
        return Event.objects.all()
    memberships = active_memberships(user, action)
    if memberships.filter(event__isnull=True).exists():
        return Event.objects.all()
    return Event.objects.filter(id__in=memberships.values("event_id"))


def can_manage_event(user, event, action="view"):
    if action not in ACTION_ROLES:
        return False
    if is_administrator(user):
        return True
    scope = Q(event__isnull=True)
    if event is not None:
        scope |= Q(event=event)
    return active_memberships(user, action).filter(scope).exists()


def can_create_events(user):
    return is_administrator(user) or active_memberships(user, "events").filter(event__isnull=True).exists()


def require_event_access(request, event_id, action="view"):
    event = get_object_or_404(Event, id=event_id)
    if not can_manage_event(request.user, event, action):
        raise PermissionDenied("Sua conta não tem permissão para esta operação neste evento.")
    return event


def management_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url="/login/")
        if not is_administrator(request.user) and not active_memberships(request.user).exists():
            raise PermissionDenied("Acesso restrito à equipe autorizada.")
        action = VIEW_ACTIONS.get(view_func.__name__)
        if action is None:
            raise PermissionDenied("Operação sem política de acesso definida.")
        if kwargs.get("event_id"):
            request.management_event = require_event_access(request, kwargs["event_id"], action)
        elif view_func.__name__ == "event_create" and not can_create_events(request.user):
            raise PermissionDenied("A criação de eventos exige acesso de administração geral.")
        elif action == "audit" and not (is_administrator(request.user) or active_memberships(request.user, "audit").exists()):
            raise PermissionDenied("Acesso restrito à auditoria.")
        return view_func(request, *args, **kwargs)
    return wrapper


def management_context(request):
    if not request.user.is_authenticated or not request.path.startswith("/gestao/"):
        return {}
    from apps.payments.connection import can_view_connection, can_manage_connection
    event = getattr(request, "management_event", None)
    events = accessible_events(request.user).order_by("-starts_at")
    if event is None:
        selected = request.GET.get("event_id") or request.session.get("management_event_id")
        try:
            if selected:
                event = events.filter(id=UUID(str(selected))).first()
        except (ValueError, TypeError, AttributeError):
            pass
        event = event or events.first()
    permissions = {action: can_manage_event(request.user, event, action) for action in ACTION_ROLES}
    permissions["events"] = permissions["events"] or can_create_events(request.user)
    role = "Administração" if is_administrator(request.user) else "Equipe do evento"
    if not is_administrator(request.user):
        scope = Q(event__isnull=True)
        if event:
            scope |= Q(event=event)
        membership = active_memberships(request.user).filter(scope).first()
        if membership:
            role = membership.get_role_code_display().split(" / ")[0]
    return {"management_event": event, "management_permissions": permissions, "management_role_label": role,
            "management_can_create_events": can_create_events(request.user),
            "management_can_view_mp": can_view_connection(request.user),
            "management_can_manage_mp": can_manage_connection(request.user)}
