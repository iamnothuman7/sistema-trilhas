from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Django Admin
    path("admin/", admin.site.urls),

    # Webhook Mercado Pago
    path("integracoes/mercadopago/webhook/", include("apps.payments.urls")),

    # Gestão / Painel da Equipe Morcego
    path("gestao/", include("apps.core.urls_gestao")),

    # Área do Piloto / Participante
    path("minha-conta/", include("apps.registrations.urls_minha_conta")),

    # Autenticação
    path("", include("apps.identity.urls")),

    # Checkout e Inscrições
    path("checkout/", include("apps.checkout.urls")),

    # Portal Público e Microsites dos Eventos
    path("", include("apps.events.urls")),
    path("", include("apps.core.urls_public")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
