from django.urls import path
from . import views
from apps.registrations.views_manual import manual_access
from .views_addons import registration_addons

app_name = "checkout"

urlpatterns = [
    path("inscricao/<uuid:registration_id>/extras/", registration_addons, name="registration_addons"),
    path("acesso-inscricao/<str:token>/", manual_access, name="manual_access"),
    path("inscricao/<slug:event_slug>/", views.start_registration, name="start_registration"),
    path("pedido/<uuid:order_id>/", views.order_payment, name="order_payment"),
    path("pedido/<uuid:order_id>/pagar/", views.process_payment_attempt, name="process_payment"),
    path("pedido/<uuid:order_id>/aguardando/", views.order_waiting, name="order_waiting"),
    path("pedido/<uuid:order_id>/status-api/", views.order_status_api, name="status_api"),
    path("pedido/<uuid:order_id>/consultar-pagamento/", views.order_refresh_payment, name="refresh_payment"),
    path("pedido/<uuid:order_id>/resultado/", views.order_result, name="order_result"),
]
