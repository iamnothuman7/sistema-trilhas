from django.urls import path
from . import views_minha_conta

app_name = "minha_conta"

urlpatterns = [
    path("", views_minha_conta.dashboard, name="dashboard"),
    path("inscricao/<uuid:registration_id>/", views_minha_conta.registration_detail, name="registration_detail"),
    path("inscricao/<uuid:registration_id>/aceitar-termos/", views_minha_conta.accept_terms, name="accept_terms"),
    path("inscricao/<uuid:registration_id>/credencial/", views_minha_conta.credential_view, name="credential_view"),
    path("reivindicar/<str:token>/", views_minha_conta.claim_ticket, name="claim_ticket"),
]
