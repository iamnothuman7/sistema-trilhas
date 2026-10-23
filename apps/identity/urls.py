from django.urls import path
from . import views
from . import views_checkout
from . import password_recovery

app_name = "identity"

urlpatterns = [
    path("login/", views.user_login, name="login"),
    path("logout/", views.user_logout, name="logout"),
    path("cadastro/", views.user_register, name="register"),
    path("compra/<uuid:order_id>/acesso/", views_checkout.purchase_access, name="purchase_access"),
    path("recuperar-senha/", password_recovery.reset_request, name="password_reset"),
    path("recuperar-senha/enviado/", password_recovery.reset_done, name="password_reset_done"),
    path("recuperar-senha/<uidb64>/<token>/", password_recovery.reset_confirm, name="password_reset_confirm"),
    path("senha-atualizada/", password_recovery.reset_complete, name="password_reset_complete"),
]
