from django.urls import path
from . import views_gestao
from apps.reporting import exports
from apps.reporting import views as reporting_views
from apps.events import views_sponsors
from apps.events import views_sponsor_leads
from apps.events import views_records, views_raffle_crud
from apps.events import views_raffles
from apps.events import views_raffle_operations
from apps.events import views_raffle_results
from apps.events.views_raffle_finance import raffle_finance
from apps.events import views_rankings
from apps.mediaframes import views_management as frame_views
from apps.payments import views_management as payment_views
from apps.catalog import views_management as catalog_views
from apps.catalog import views_stock
from .private_documents import private_document_download
from apps.registrations import views_manual
from apps.registrations import views_cleanup
from apps.registrations import views_management as registration_views
from apps.finance import views_management as finance_views
from apps.finance import views_crud as finance_crud
from apps.identity import views_team
from .management_navigation import switch_management_event

app_name = "gestao"

urlpatterns = [
    path('eventos/<uuid:event_id>/excluir/',views_records.event_action,name='event_remove'),
    path('eventos/<uuid:event_id>/restaurar/',views_records.event_action,{'restore':True},name='event_restore'),
    path('eventos/<uuid:event_id>/catalogo/excluidos/',catalog_views.catalog_trash,name='catalog_trash'),
    path('eventos/<uuid:event_id>/catalogo/<str:kind>/<str:object_id>/excluir/',catalog_views.catalog_action,name='catalog_remove'),
    path('eventos/<uuid:event_id>/catalogo/<str:kind>/<str:object_id>/restaurar/',catalog_views.catalog_action,{'restore':True},name='catalog_restore'),
    path('eventos/<uuid:event_id>/equipe/<uuid:user_id>/editar/',views_team.team_edit,name='team_edit'),
    path('patrocinadores/<int:profile_id>/excluir/',views_records.sponsor_profile_action,name='sponsor_profile_remove'),
    path('patrocinadores/<int:profile_id>/restaurar/',views_records.sponsor_profile_action,{'restore':True},name='sponsor_profile_restore'),
    path('eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/excluir/',views_records.sponsor_action,name='sponsor_remove'),
    path('eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/restaurar/',views_records.sponsor_action,{'restore':True},name='sponsor_restore'),
    path('eventos/<uuid:event_id>/patrocinadores/historico/<int:history_id>/editar/',views_records.sponsor_history_editor,name='sponsor_history_edit'),
    path('eventos/<uuid:event_id>/patrocinadores/historico/<int:history_id>/excluir/',views_records.sponsor_history_editor,{'remove':True},name='sponsor_history_remove'),
    path('eventos/<uuid:event_id>/patrocinadores/<int:sponsor_id>/recebimentos/<uuid:payment_id>/editar/',views_records.sponsor_payment_editor,name='sponsor_payment_edit'),
    path('eventos/<uuid:event_id>/sorteio/pontos/<uuid:point_id>/editar/',views_raffle_crud.raffle_point_editor,name='raffle_point_edit'),
    path('eventos/<uuid:event_id>/sorteio/pontos/<uuid:point_id>/excluir/',views_raffle_crud.raffle_point_editor,{'remove':True},name='raffle_point_remove'),
    path('eventos/<uuid:event_id>/sorteio/bilhetes/<uuid:ticket_id>/editar/',views_raffle_crud.raffle_ticket_editor,name='raffle_ticket_edit'),
    path('eventos/<uuid:event_id>/sorteio/bilhetes/<uuid:ticket_id>/excluir/',views_raffle_crud.raffle_ticket_editor,{'remove':True},name='raffle_ticket_remove'),
    path("eventos/<uuid:event_id>/equipe/", views_team.team_management, name="team_management"),
    path("eventos/<uuid:event_id>/equipe/<uuid:user_id>/remover/", views_team.team_revoke, name="team_revoke"),
    path("selecionar-evento/", switch_management_event, name="switch_event"),
    path("como-usar/", views_gestao.usage_guide, name="usage_guide"),
    # Dashboard Principal
    path("", views_gestao.dashboard, name="dashboard"),
    path("integracoes/mercado-pago/", payment_views.mercadopago_connection, name="mercadopago_connection"),
    path("integracoes/mercado-pago/taxas/", payment_views.mercadopago_fees, name="mercadopago_fees"),

    # Eventos
    path("eventos/", views_gestao.event_list, name="event_list"),
    path("eventos/novo/", views_gestao.event_create, name="event_create"),
    path("eventos/<uuid:event_id>/", views_gestao.event_detail, name="event_detail"),
    path("eventos/<uuid:event_id>/editar/", views_gestao.event_edit, name="event_edit"),
    path("eventos/<uuid:event_id>/aparencia/", views_gestao.event_appearance, name="event_appearance"),
    path("eventos/<uuid:event_id>/duplicar/", views_gestao.event_duplicate, name="event_duplicate"),

    # Inscrições
    path('eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/editar/',registration_views.registration_edit,name='registration_edit'),
    path('eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/devolucao/',registration_views.registration_refund,name='registration_refund'),
    path("eventos/<uuid:event_id>/inscricoes/<uuid:reg_id>/remover/", views_cleanup.registration_remove, name="registration_remove"),
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
    path('eventos/<uuid:event_id>/camisetas/',views_stock.shirt_stock,name='shirt_stock'),
    path("eventos/<uuid:event_id>/catalogo/", views_gestao.catalog_management, name="catalog"),
    path("eventos/<uuid:event_id>/catalogo/lotes/<uuid:lot_id>/remover/", catalog_views.lot_remove, name="lot_remove"),
    path("eventos/<uuid:event_id>/catalogo/<str:kind>/novo/", catalog_views.catalog_editor, name="catalog_create"),
    path("eventos/<uuid:event_id>/catalogo/<str:kind>/<str:object_id>/editar/", catalog_views.catalog_editor, name="catalog_edit"),

    # Financeiro Gerencial
    path('eventos/<uuid:event_id>/financeiro/movimentacoes/',finance_crud.movement_list,name='movement_list'),
    path('eventos/<uuid:event_id>/financeiro/movimentacoes/nova/',finance_crud.movement_editor,name='movement_create'),
    path('eventos/<uuid:event_id>/financeiro/movimentacoes/<uuid:movement_id>/editar/',finance_crud.movement_editor,name='movement_edit'),
    path('eventos/<uuid:event_id>/financeiro/movimentacoes/<uuid:movement_id>/excluir/',finance_crud.movement_editor,{'operation':'remove'},name='movement_remove'),
    path('eventos/<uuid:event_id>/financeiro/movimentacoes/<uuid:movement_id>/restaurar/',finance_crud.movement_editor,{'operation':'restore'},name='movement_restore'),
    path('eventos/<uuid:event_id>/financeiro/fornecedores/',finance_crud.vendor_list,name='vendor_list'),
    path('eventos/<uuid:event_id>/financeiro/fornecedores/novo/',finance_crud.vendor_editor,name='vendor_create'),
    path('eventos/<uuid:event_id>/financeiro/fornecedores/<uuid:vendor_id>/editar/',finance_crud.vendor_editor,name='vendor_edit'),
    path('eventos/<uuid:event_id>/financeiro/fornecedores/<uuid:vendor_id>/excluir/',finance_crud.vendor_editor,{'operation':'remove'},name='vendor_remove'),
    path('eventos/<uuid:event_id>/financeiro/fornecedores/<uuid:vendor_id>/restaurar/',finance_crud.vendor_editor,{'operation':'restore'},name='vendor_restore'),
    path('eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/excluir/',finance_crud.expense_action,name='expense_remove'),
    path('eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/restaurar/',finance_crud.expense_action,{'operation':'restore'},name='expense_restore'),
    path('eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/situacao/',finance_views.expense_status,name='expense_status'),
    path('eventos/<uuid:event_id>/financeiro/devolucoes/<uuid:refund_id>/conferir/',registration_views.refund_channel_review,name='refund_channel_review'),
    path('eventos/<uuid:event_id>/financeiro/adicionais/<uuid:order_id>/devolucao/',registration_views.addon_order_refund,name='addon_order_refund'),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/editar/", finance_views.expense_editor, name="expense_edit"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/comprovante/", finance_views.expense_editor, {'operation':'proof'}, name="expense_proof"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/pagamento/", finance_views.expense_editor, {'operation':'pay'}, name="expense_pay"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/anular/", finance_views.expense_editor, {'operation':'void'}, name="expense_void"),
    path("eventos/<uuid:event_id>/documentos/<str:kind>/<uuid:document_id>/", private_document_download, name="private_document_download"),
    path("eventos/<uuid:event_id>/financeiro/", views_gestao.finance_overview, name="finance"),
    path("eventos/<uuid:event_id>/financeiro/despesa/nova/", views_gestao.add_expense, name="add_expense"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/pagar/", views_gestao.pay_expense, name="pay_expense"),
    path("eventos/<uuid:event_id>/financeiro/despesas/<uuid:expense_id>/cancelar/", views_gestao.cancel_expense, name="cancel_expense"),
    path("eventos/<uuid:event_id>/relatorios/", reporting_views.event_reports, name="reports"),
    path("eventos/<uuid:event_id>/ranking/", views_rankings.ranking_management, name="ranking_management"),

    # Molduras
    path("eventos/<uuid:event_id>/molduras/", views_gestao.frame_management, name="frames"),
    path("eventos/<uuid:event_id>/molduras/oficiais/", frame_views.official_frame_preview, name="official_frame_preview"),
    path("eventos/<uuid:event_id>/molduras/equipes/", frame_views.team_frame_editor, name="team_frame_editor"),

    # Patrocinadores
    path('eventos/<uuid:event_id>/patrocinadores/interessados/',views_sponsor_leads.sponsor_leads,name='sponsor_leads'),
    path('eventos/<uuid:event_id>/patrocinadores/interessados/novo/',views_sponsor_leads.sponsor_lead_edit,name='sponsor_lead_create'),
    path('eventos/<uuid:event_id>/patrocinadores/interessados/<int:lead_id>/editar/',views_sponsor_leads.sponsor_lead_edit,name='sponsor_lead_edit'),
    path('eventos/<uuid:event_id>/patrocinadores/interessados/<int:lead_id>/confirmar/',views_sponsor_leads.sponsor_lead_convert,name='sponsor_lead_convert'),
    path('eventos/<uuid:event_id>/patrocinadores/interessados/<int:lead_id>/remover/',views_sponsor_leads.sponsor_lead_archive,name='sponsor_lead_remove'),
    path('eventos/<uuid:event_id>/patrocinadores/interessados/<int:lead_id>/restaurar/',views_sponsor_leads.sponsor_lead_archive,{'restore':True},name='sponsor_lead_restore'),
    path('eventos/<uuid:event_id>/patrocinadores/marcas/<int:profile_id>/historico/',views_sponsor_leads.sponsor_history,name='sponsor_history'),
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
    path('eventos/<uuid:event_id>/sorteio/resultado/',views_raffle_results.raffle_result,name='raffle_result'),
    path("eventos/<uuid:event_id>/sorteio/financeiro/", raffle_finance, name="raffle_finance"),
    path('eventos/<uuid:event_id>/sorteio/pedidos/<uuid:order_id>/devolucao/',registration_views.raffle_order_refund,name='raffle_order_refund'),
    path("eventos/<uuid:event_id>/sorteio/operacao/", views_raffle_operations.raffle_operations, name="raffle_operations"),
    path("eventos/<uuid:event_id>/sorteio/imprimir.pdf", views_raffle_operations.raffle_print, name="raffle_print"),

    # Exportações
    path("eventos/<uuid:event_id>/exportar/inscritos/", views_gestao.export_participants, name="export_participants"),
    path("eventos/<uuid:event_id>/exportar/financeiro/", views_gestao.export_financial, name="export_financial"),

    # Auditoria
    path("auditoria/", views_gestao.audit_log_view, name="audit_log"),
]
