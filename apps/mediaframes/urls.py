from django.urls import path
from . import views

app_name = "mediaframes"

urlpatterns = [
    path("api/remove-background/", views.remove_photo_background, name="remove-background"),
    path("api/montages/<uuid:montage_id>/backup/", views.backup_montage, name="montage-backup"),
    path("api/montages/<uuid:montage_id>/download/", views.deliver_download, name="montage-download"),
]
