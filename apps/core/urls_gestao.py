from django.urls import path
from . import views_gestao
from apps.reporting import exports
from apps.reporting import views as reporting_views
from apps.events import views_sponsors
from apps.events import views_raffles
from apps.events import views_rankings
from apps.mediaframes import views_management as frame_views
from apps.payments import views_management as payment_views
from apps.catalog import views_management as catalog_views
from .private_documents import private_document_download
from apps.registrations import views_manual

app_name = "gestao"

urlpatterns = [
    path("como-usar/", views_gestao.usage_guide, name="usage_guide"),
    # Dashboard Principal
    path("", views_gestao.dashboard, name="dashboard"),
    path("integracoes/mercado-pago/", payment_views.mercadopago_connection, name="mercadopago_connection"),

    # Eventos
    path("eventos/", views_gestao.event_list, name="event_list"),
    path("eventos/novo/", views_gestao.event_create, name="event_create"),
    path("eventos/<uuid:event_id>/", views_gestao.event_detail, name="event_detail"),
    path("eventos/<uuid:event_id>/editar/", views_gestao.event_edit, name="event_edit"),
    path("eventos/<uuid:event_id>/aparencia/", views_gestao.event_appearance, name="event_appearance"),
    path("eventos/<uuid:event_id>/duplicar/", views_gestao.event_duplicate, name="event_duplicate"),

    # Inscrições
    path("eventos/<uuid:event_id>/inscricoes/manual/", views_manual.manual_registration_create, name="manual_registration_create"),
    path("eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/recebimento/", views_manual.manual_registration_payment, name="manual_registration_payment"),
    path("eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/renovar-acesso/", views_manual.manual_registration_renew_link, name="manual_registration_renew_link"),
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
    path("eventos/<uuid:event_id>/catalogo/<str:kind>/novo/", catalog_views.catalog_editor, name="catalog_create"),
    path("eventos/<uuid:event_id>/catalogo/<str:kind>/<str:object_id>/editar/", catalog_views.catalog_editor, name="catalog_edit"),

    # Financeiro Gerencial
    path("eventos/<uuid:event_id>/documentos/<str:kind>/<uuid:document_id>/", private_document_download, name="private_document_download"),
    path("eventos/<uuid:event_id>/financeiro/", views_gestao.finance_overview, name="finance"),
    path("eventos/<uuid:event_id>/financeiro/despesa/nova/", views_gestao.add_expense, name="add_expense"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/pagar/", views_gestao.pay_expense, name="pay_expense"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/cancelar/", views_gestao.cancel_expense, name="cancel_expense"),
    path("eventos/<uuid:event_id>/relatorios/", reporting_views.event_reports, name="reports"),
    path("eventos/<uuid:event_id>/ranking/", views_rankings.ranking_management, name="ranking_management"),

    # Expedição
    path("eventos/<uuid:event_id>/expedicao/", views_gestao.expedition_management, name="expedition"),

    # Molduras
    path("eventos/<uuid:event_id>/molduras/", views_gestao.frame_management, name="frames"),
    path("eventos/<uuid:event_id>/molduras/equipes/", frame_views.team_frame_editor, name="team_frame_editor"),

    # Patrocinadores
    path("patrocinadores/", views_sponsors.sponsor_bank, name="sponsor_bank"),
    path("patrocinadores/novo/", views_sponsors.sponsor_profile_create, name="sponsor_profile_create"),
    path("patrocinadores/<int:profile_id>/editar/", views_sponsors.sponsor_profile_edit, name="sponsor_profile_edit"),
    path("eventos/<uuid:event_id>/patrocinadores/", views_sponsors.sponsor_list, name="sponsor_list"),
    path("eventos/<uuid:event_id>/patrocinadores/novo/", views_sponsors.sponsor_create, name="sponsor_create"),
    path("eventos/<uuid:event_id>/patrocinadores/vincular/", views_sponsors.sponsor_link, name="sponsor_link"),
    path("eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/", views_sponsors.sponsor_detail, name="sponsor_detail"),
    path("eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/editar/", views_sponsors.sponsor_edit, name="sponsor_edit"),
    path("eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/acordo/", views_sponsors.sponsor_agreement, name="sponsor_agreement"),
    path("eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/receber/", views_sponsors.sponsor_receipt, name="sponsor_receipt"),
    path("eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/recebimentos/<uuid:payment_id>/reverter/", views_sponsors.sponsor_reversal, name="sponsor_reversal"),
    path("eventos/<uuid:event_id>/sorteio/", views_raffles.raffle_management, name="raffle_management"),

    # Exportações
    path("eventos/<uuid:event_id>/exportar/inscritos/", views_gestao.export_participants, name="export_participants"),
    path("eventos/<uuid:event_id>/exportar/financeiro/", views_gestao.export_financial, name="export_financial"),

    # Auditoria
    path("auditoria/", views_gestao.audit_log_view, name="audit_log"),
]
