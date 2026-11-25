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

TAB_DEFINITIONS = [
    {
        "id": "dashboard",
        "name": "Painel Geral",
        "icon": "layout-dashboard",
        "header": "ACESSOS NO PAINEL GERAL",
        "actions": [
            ("view", "Visualizar Métricas", "Total arrecadado, inscritos e kits"),
            ("export", "Exportar Relatórios", "Baixar balanços e demonstrativos"),
        ],
    },
    {
        "id": "registrations",
        "name": "Inscrições",
        "icon": "users-round",
        "header": "ACESSOS EM VENDAS & INSCRIÇÕES",
        "actions": [
            ("view", "Visualizar Inscrições", "Consultar lista de inscritos e buscas"),
            ("create", "Nova Inscrição / Emitir", "Cadastrar participantes e vendas balcão"),
            ("edit", "Editar Dados & Camiseta", "Alterar dados cadastrais, moto e tamanho"),
            ("cancel", "Cancelar / Estornar Vendas", "Cancelar inscrições e estornos"),
            ("print", "Imprimir Comprovante / Carnê", "Emitir e imprimir via térmica ou PDF"),
            ("export", "Exportar Planilha CSV", "Baixar listagem completa de inscritos"),
        ],
    },
    {
        "id": "checkin",
        "name": "Credenciamento",
        "icon": "scan-line",
        "header": "ACESSOS EM CREDENCIAMENTO & PORTARIA",
        "actions": [
            ("scan", "Scanner QR Code", "Ler ingressos com câmera do celular"),
            ("search", "Busca Manual", "Pesquisar participante por nome/CPF"),
            ("checkin", "Confirmar Check-in", "Validar entrada e presença na trilha"),
            ("deliver_kit", "Entregar Kit / Camiseta", "Registrar entrega do kit oficial"),
            ("deliver_addon", "Entregar Produtos Extras", "Entregar troféus, bonés e compras extras"),
        ],
    },
    {
        "id": "catalog",
        "name": "Lotes & Cupons",
        "icon": "ticket",
        "header": "ACESSOS EM LOTES & CUPONS",
        "actions": [
            ("view", "Visualizar Lotes & Cupons", "Consultar lotes vigentes e cupons"),
            ("create", "Criar Lotes / Cupons", "Cadastrar novos lotes ou descontos"),
            ("edit", "Editar Valores e Prazos", "Alterar valores, vagas e datas"),
            ("delete", "Excluir Lotes", "Remover ou desativar lotes"),
        ],
    },
    {
        "id": "addons",
        "name": "Produtos Extras",
        "icon": "shopping-bag",
        "header": "ACESSOS EM PRODUTOS EXTRAS & LOJA",
        "actions": [
            ("view", "Visualizar Produtos Extras", "Consultar estoque e catálogo"),
            ("create", "Cadastrar Novo Produto", "Adicionar produtos à venda no site"),
            ("edit", "Editar Preço e Estoque", "Alterar valores, limites e fotos"),
            ("delete", "Excluir Produtos", "Remover produtos do catálogo"),
        ],
    },
    {
        "id": "shirts",
        "name": "Camisetas",
        "icon": "shirt",
        "header": "ACESSOS NO ESTOQUE DE CAMISETAS",
        "actions": [
            ("view", "Visualizar Grade & Balanço", "Ver camisetas pedidas e saldo"),
            ("adjust", "Lançar Entrada Fornecedor", "Registrar remessas da confecção"),
        ],
    },
    {
        "id": "raffle",
        "name": "Sorteio da Moto",
        "icon": "sparkles",
        "header": "ACESSOS NO SORTEIO DE PRÊMIOS",
        "actions": [
            ("view", "Visualizar Números da Sorte", "Consultar cartelas e números"),
            ("search", "Buscar Número / Ganhador", "Localizar cartela premiada"),
            ("draw", "Realizar Apuração / Sorteio", "Executar apuração ao vivo"),
            ("export", "Exportar Lista / Globo", "Baixar números e PDF conferência"),
            ("config", "Configurar Regras & Prêmios", "Alterar premiação e regras"),
        ],
    },
    {
        "id": "rankings",
        "name": "Equipes & Ranking",
        "icon": "trophy",
        "header": "ACESSOS EM EQUIPES & RANKING",
        "actions": [
            ("view", "Visualizar Ranking", "Ver classificação das maiores equipes"),
            ("unify", "Unificar Nomes de Equipes", "Agrupar grafias da mesma equipe"),
        ],
    },
    {
        "id": "finance",
        "name": "Financeiro & Caixa",
        "icon": "wallet",
        "header": "ACESSOS NO FINANCEIRO & CAIXA",
        "actions": [
            ("view", "Visualizar Saldo & Extrato", "Ver faturamento, saldo e extrato"),
            ("add_expense", "Lançar Despesa / Saída", "Cadastrar contas a pagar"),
            ("pay_expense", "Dar Baixa com Recibo", "Confirmar pagamento e anexar recibo"),
            ("refund", "Autorizar Estornos", "Aprovar devoluções e reembolsos"),
            ("export", "Exportar Conciliação", "Baixar extrato e fluxo de caixa"),
        ],
    },
    {
        "id": "frames",
        "name": "Molduras Digitais",
        "icon": "image",
        "header": "ACESSOS EM MOLDURAS & STUDIO",
        "actions": [
            ("view", "Visualizar Molduras", "Ver molduras ativas para Feed/Story"),
            ("upload", "Cadastrar Molduras PNG", "Fazer upload de artes sem fundo"),
            ("delete", "Excluir Molduras", "Remover artes e molduras antigas"),
        ],
    },
]

ROLE_PRESETS = {
    "ADMIN": {
        "label": "Administrador Total (Acesso Completo)",
        "role_code": RoleChoices.EVENT_MANAGER,
        "permissions": {tab["id"]: [act[0] for act in tab["actions"]] for tab in TAB_DEFINITIONS},
    },
    "PDV": {
        "label": "Vendedor de Ponto (PDV - Apenas Emitir e Imprimir)",
        "role_code": RoleChoices.REGISTRATION_AGENT,
        "permissions": {
            "dashboard": ["view"],
            "registrations": ["view", "create", "print"],
            "checkin": [],
            "catalog": [],
            "addons": [],
            "shirts": [],
            "raffle": [],
            "rankings": [],
            "finance": [],
            "frames": [],
        },
    },
    "VENDEDOR": {
        "label": "Vendedor / Inscrições Balcão",
        "role_code": RoleChoices.REGISTRATION_AGENT,
        "permissions": {
            "dashboard": ["view"],
            "registrations": ["view", "create", "print"],
            "checkin": [],
            "catalog": ["view"],
            "addons": ["view"],
            "shirts": ["view"],
            "raffle": ["view"],
            "rankings": ["view"],
            "finance": [],
            "frames": ["view"],
        },
    },
    "CREDENCIAMENTO": {
        "label": "Operador de Credenciamento (Portaria)",
        "role_code": RoleChoices.CHECKIN_AGENT,
        "permissions": {
            "dashboard": ["view"],
            "registrations": ["view", "print"],
            "checkin": ["scan", "search", "checkin", "deliver_kit", "deliver_addon"],
            "catalog": [],
            "addons": ["view"],
            "shirts": ["view"],
            "raffle": ["view", "search"],
            "rankings": ["view"],
            "finance": [],
            "frames": ["view"],
        },
    },
    "FINANCEIRO": {
        "label": "Caixa / Financeiro",
        "role_code": RoleChoices.FINANCE,
        "permissions": {
            "dashboard": ["view", "export"],
            "registrations": ["view", "export"],
            "checkin": [],
            "catalog": ["view"],
            "addons": ["view"],
            "shirts": ["view"],
            "raffle": ["view"],
            "rankings": ["view"],
            "finance": ["view", "add_expense", "pay_expense", "refund", "export"],
            "frames": [],
        },
    },
    "PISTA": {
        "label": "Fiscal de Pista / Apoio & Sorteio",
        "role_code": RoleChoices.SAFETY,
        "permissions": {
            "dashboard": ["view"],
            "registrations": ["view"],
            "checkin": ["scan", "search"],
            "catalog": [],
            "addons": [],
            "shirts": [],
            "raffle": ["view", "search", "draw"],
            "rankings": ["view"],
            "finance": [],
            "frames": ["view"],
        },
    },
    "CUSTOM": {
        "label": "Personalizado",
        "role_code": RoleChoices.CUSTOM,
        "permissions": {},
    },
}

ACTION_ROLES = {
    "view": set(RoleChoices.values),
    "events": MANAGERS,
    "registrations": MANAGERS | {RoleChoices.REGISTRATION_AGENT, RoleChoices.CUSTOM},
    "checkin": MANAGERS | {RoleChoices.CHECKIN_AGENT, RoleChoices.REGISTRATION_AGENT, RoleChoices.CUSTOM},
    "finance": MANAGERS | {RoleChoices.FINANCE, RoleChoices.CUSTOM},
    "catalog": MANAGERS | {RoleChoices.CUSTOM},
    "content": MANAGERS | {RoleChoices.CONTENT, RoleChoices.CUSTOM},
    "sponsors": MANAGERS | {RoleChoices.CONTENT, RoleChoices.FINANCE, RoleChoices.CUSTOM},
    "audit": MANAGERS,
    "team": MANAGERS,
}
VIEW_ACTIONS = {
    'access_recovery':'team',
    'raffle_overview':'events','raffle_sellers':'events','raffle_seller_detail':'events',
    'event_action':'events','catalog_trash':'catalog','catalog_action':'catalog','team_edit':'team',
    'sponsor_action':'content','sponsor_profile_action':'content','sponsor_history_editor':'finance','sponsor_payment_editor':'finance',
    'raffle_point_editor':'events','raffle_ticket_editor':'finance',
    'movement_list':'finance','movement_editor':'finance','vendor_list':'finance','vendor_editor':'finance','expense_action':'finance',
    'sponsor_lead_archive':'content',
    'raffle_result':'events',
    'sponsor_leads':'sponsors','sponsor_lead_edit':'content','sponsor_lead_convert':'content','sponsor_history':'finance',
    'shirt_stock':'catalog',
    'expense_status':'finance',
    'refund_channel_review':'finance',
    'addon_order_refund':'finance',
    'raffle_order_refund':'finance',
    "expense_editor": "finance", "raffle_finance": "finance", "registration_edit": "registrations", "registration_refund": "finance",
    "team_management": "team", "team_revoke": "team",
    "registration_remove": "registrations",
    "reset_test_data_view": "registrations",
    "manual_registration_create": "registrations",
    "manual_registration_payment": "finance",
    "manual_registration_renew_link": "registrations",
    "private_document_download": "finance",
    "catalog_editor": "catalog",
    "lot_remove": "catalog",
    "switch_management_event": "view",
    "usage_guide": "view",
    "analytics_overview": "view",
    "dashboard": "view", "event_list": "events", "event_create": "events",
    "event_detail": "events", "event_edit": "events", "event_duplicate": "events", "event_appearance": "content",
    "registration_list": "registrations", "registration_detail": "registrations",
    "registration_change_status": "registrations", "export_participants": "registrations",
    "credenciamento_scanner": "checkin", "credenciamento_lookup_api": "checkin",
    "credenciamento_confirm_api": "checkin", "deliver_benefit_api": "checkin",
    "catalog_management": "catalog", "addons_management": "catalog", "finance_overview": "finance", "add_expense": "finance",
    "pay_expense": "finance", "cancel_expense": "finance", "export_financial": "finance",
    "event_reports": "finance", "frame_management": "content",
    "audit_log_view": "audit",
    "sponsor_list": "sponsors", "sponsor_create": "content", "sponsor_edit": "content",
    "sponsor_bank": "sponsors", "sponsor_profile_create": "content", "sponsor_profile_edit": "content",
    "sponsor_link": "content", "sponsor_detail": "sponsors", "sponsor_agreement": "finance",
    "sponsor_receipt": "finance", "sponsor_reversal": "finance",
    "raffle_management": "events",
    "raffle_operations": "events", "raffle_print": "events",
    "team_frame_editor": "content",
    "official_frame_preview": "content",
    "ranking_management": "registrations",
}


def is_administrator(user):
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def get_user_event_permissions(user, event):
    """
    Retorna dicionário {tab_id: set([action, ...])} para o usuário no evento.
    Superusuários e gerentes possuem todas as permissões liberadas.
    """
    all_perms = {tab["id"]: {act[0] for act in tab["actions"]} for tab in TAB_DEFINITIONS}
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return {tab["id"]: set() for tab in TAB_DEFINITIONS}
    
    if is_administrator(user):
        return all_perms

    scope = Q(event__isnull=True)
    if event is not None:
        scope |= Q(event=event)
    memberships = EventMembership.objects.filter(user=user, revoked_at__isnull=True).filter(scope)
    
    if not memberships.exists():
        return {tab["id"]: set() for tab in TAB_DEFINITIONS}

    for m in memberships:
        if m.role_code in MANAGERS:
            return all_perms

    user_perms = {tab["id"]: set() for tab in TAB_DEFINITIONS}
    for m in memberships:
        if m.permissions_json and isinstance(m.permissions_json, dict) and any(m.permissions_json.values()):
            for tab_id, acts in m.permissions_json.items():
                if tab_id in user_perms and isinstance(acts, (list, set, tuple)):
                    user_perms[tab_id].update(acts)
        else:
            default_map = {
                RoleChoices.REGISTRATION_AGENT: {
                    "dashboard": {"view", "export"},
                    "registrations": {"view", "create", "edit", "cancel", "print", "export"},
                    "checkin": {"scan", "search", "checkin", "deliver_kit", "deliver_addon"},
                    "rankings": {"view", "unify"},
                },
                RoleChoices.CHECKIN_AGENT: {
                    "dashboard": {"view"},
                    "checkin": {"scan", "search", "checkin", "deliver_kit", "deliver_addon"},
                },
                RoleChoices.FINANCE: {
                    "dashboard": {"view", "export"},
                    "registrations": {"view", "export"},
                    "finance": {"view", "add_expense", "pay_expense", "refund", "export"},
                },
                RoleChoices.CONTENT: {
                    "dashboard": {"view"},
                    "frames": {"view", "upload", "delete"},
                },
                RoleChoices.SAFETY: {
                    "dashboard": {"view"},
                    "checkin": {"scan", "search"},
                    "raffle": {"view", "search", "draw"},
                },
                RoleChoices.LOGISTICS: {
                    "dashboard": {"view"},
                },
            }
            role_p = default_map.get(m.role_code, {})
            for tab_id, acts in role_p.items():
                if tab_id in user_perms:
                    user_perms[tab_id].update(acts)

    return user_perms


def has_event_permission(user, event, tab, action):
    perms = get_user_event_permissions(user, event)
    return action in perms.get(tab, set())


def active_memberships(user, action="view"):
    if not user.is_authenticated or not user.is_active:
        return EventMembership.objects.none()
    qs = EventMembership.objects.filter(user=user, revoked_at__isnull=True)
    if action == "view":
        return qs
    return qs.filter(role_code__in=ACTION_ROLES.get(action, set()))


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
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False  # visitante sem login nunca gerencia (evita erro 500 em páginas de rascunho)
    if is_administrator(user):
        return True

    scope = Q(event__isnull=True)
    if event is not None:
        scope |= Q(event=event)
    memberships = EventMembership.objects.filter(user=user, revoked_at__isnull=True).filter(scope)
    if not memberships.exists():
        return False

    if memberships.filter(role_code__in=MANAGERS).exists():
        return True

    # If any membership has custom non-empty permissions_json, check granular
    has_custom = any(bool(m.permissions_json and any(m.permissions_json.values())) for m in memberships)
    if has_custom:
        user_perms = get_user_event_permissions(user, event)
        if action == "view":
            return any(len(acts) > 0 for acts in user_perms.values())
        elif action == "registrations":
            return len(user_perms.get("registrations", set())) > 0 or len(user_perms.get("rankings", set())) > 0
        elif action == "checkin":
            return len(user_perms.get("checkin", set())) > 0
        elif action == "finance":
            return len(user_perms.get("finance", set())) > 0
        elif action == "catalog":
            return len(user_perms.get("catalog", set())) > 0 or len(user_perms.get("addons", set())) > 0 or len(user_perms.get("shirts", set())) > 0
        elif action == "content":
            return len(user_perms.get("frames", set())) > 0
        elif action == "sponsors":
            return len(user_perms.get("finance", set())) > 0 or len(user_perms.get("frames", set())) > 0
        elif action in ("team", "audit", "events"):
            return False

    return memberships.filter(role_code__in=ACTION_ROLES.get(action, set())).exists()


def can_create_events(user):
    return is_administrator(user) or active_memberships(user, "events").filter(event__isnull=True).exists()


def require_event_access(request, event_id, action="view"):
    event = get_object_or_404(Event, id=event_id)
    if not can_manage_event(request.user, event, action):
        raise PermissionDenied("Sua conta não tem permissão para esta operação neste evento.")
    return event


def selected_management_event(request, action="view"):
    """Resolve explicit context first; never silently fall back from a forbidden ID."""
    event = getattr(request, "management_event", None)
    if event is not None:
        return event
    events = accessible_events(request.user, action).order_by("-starts_at", "pk")
    selected = request.GET.get("event_id")
    if selected and selected != "all":
        try:
            event = require_event_access(request, UUID(selected), action)
        except (ValueError, TypeError, AttributeError):
            from django.http import Http404
            raise Http404("Evento não encontrado.")
    else:
        try:
            remembered = UUID(str(request.session.get("management_event_id")))
            event = events.filter(pk=remembered).first()
        except (ValueError, TypeError, AttributeError):
            event = None
        event = event or events.first()
    if event:
        request.management_event = event
        request.session["management_event_id"] = str(event.pk)
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
            request.session["management_event_id"] = str(request.management_event.pk)
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
    event = selected_management_event(request)
    permissions = {action: can_manage_event(request.user, event, action) for action in ACTION_ROLES}
    permissions["events"] = permissions["events"] or can_create_events(request.user)
    role = "Administração" if is_administrator(request.user) else "Equipe do evento"
    if not is_administrator(request.user):
        scope = Q(event__isnull=True)
        if event:
            scope |= Q(event=event)
        membership = active_memberships(request.user).filter(scope).first()
        if membership:
            if membership.role_code == RoleChoices.CUSTOM:
                role = "Personalizado"
            else:
                role = membership.get_role_code_display().split(" / ")[0]

    raw_user_perms = get_user_event_permissions(request.user, event)
    user_perms = {}
    for tab in TAB_DEFINITIONS:
        tab_id = tab["id"]
        allowed = raw_user_perms.get(tab_id, set())
        user_perms[tab_id] = {act[0]: (act[0] in allowed) for act in tab["actions"]}

    from .management_navigation import event_switcher_context
    return {
        **event_switcher_context(request, event),
        "management_event": event,
        "management_permissions": permissions,
        "user_perms": user_perms,
        "raw_user_perms": raw_user_perms,
        "management_role_label": role,
        "management_can_create_events": can_create_events(request.user),
        "management_can_view_mp": can_view_connection(request.user),
        "management_can_manage_mp": can_manage_connection(request.user)
    }
