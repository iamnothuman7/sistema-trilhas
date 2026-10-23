from django.urls import path
from . import views_minha_conta

app_name = "minha_conta"

urlpatterns = [
    path("", views_minha_conta.dashboard, name="dashboard"),
    path("inscricao/<uuid:registration_id>/", views_minha_conta.registration_detail, name="registration_detail"),
    path("inscricao/<uuid:registration_id>/aceitar-termos/", views_minha_conta.accept_terms, name="accept_terms"),
    path("inscricao/<uuid:registration_id>/credencial/", views_minha_conta.credential_view, name="credential_view"),
    path("inscricao/<uuid:registration_id>/comprovante.pdf", views_minha_conta.registration_pdf, name="registration_pdf"),
    path("inscricao/<uuid:registration_id>/compartilhar/", views_minha_conta.create_claim, name="create_claim"),
    path("reivindicar/<str:token>/", views_minha_conta.claim_ticket, name="claim_ticket"),
]
