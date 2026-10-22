from django.urls import path
from . import views_gestao
from apps.reporting import exports

app_name = "gestao"

urlpatterns = [
    # Dashboard Principal
    path("", views_gestao.dashboard, name="dashboard"),

    # Eventos
    path("eventos/", views_gestao.event_list, name="event_list"),
    path("eventos/novo/", views_gestao.event_create, name="event_create"),
    path("eventos/<uuid:event_id>/", views_gestao.event_detail, name="event_detail"),
    path("eventos/<uuid:event_id>/editar/", views_gestao.event_edit, name="event_edit"),
    path("eventos/<uuid:event_id>/duplicar/", views_gestao.event_duplicate, name="event_duplicate"),

    # Inscrições
    path("eventos/<uuid:event_id>/inscricoes/", views_gestao.registration_list, name="registration_list"),
    path("eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/", views_gestao.registration_detail, name="registration_detail"),
    path("eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/status/", views_gestao.registration_change_status, name="registration_change_status"),

    # Credenciamento & Scanner Mobile
    path("eventos/<uuid:event_id>/credenciamento/", views_gestao.credenciamento_scanner, name="credenciamento"),
    path("eventos/<uuid:event_id>/credenciamento/lookup-api/", views_gestao.credenciamento_lookup_api, name="credenciamento_lookup"),
    path("eventos/<uuid:event_id>/credenciamento/confirm-api/", views_gestao.credenciamento_confirm_api, name="credenciamento_confirm"),
    path("eventos/<uuid:event_id>/credenciamento/deliver-benefit-api/", views_gestao.deliver_benefit_api, name="deliver_benefit"),

    # Catálogo e Lotes
    path("eventos/<uuid:event_id>/catalogo/", views_gestao.catalog_management, name="catalog"),

    # Financeiro Gerencial
    path("eventos/<uuid:event_id>/financeiro/", views_gestao.finance_overview, name="finance"),
    path("eventos/<uuid:event_id>/financeiro/despesa/nova/", views_gestao.add_expense, name="add_expense"),

    # Expedição
    path("eventos/<uuid:event_id>/expedicao/", views_gestao.expedition_management, name="expedition"),

    # Molduras
    path("eventos/<uuid:event_id>/molduras/", views_gestao.frame_management, name="frames"),

    # Exportações
    path("eventos/<uuid:event_id>/exportar/inscritos/", views_gestao.export_participants, name="export_participants"),
    path("eventos/<uuid:event_id>/exportar/financeiro/", views_gestao.export_financial, name="export_financial"),

    # Auditoria
    path("auditoria/", views_gestao.audit_log_view, name="audit_log"),
]
