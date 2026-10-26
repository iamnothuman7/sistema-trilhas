from django.urls import path
from . import views

app_name = "mediaframes"

urlpatterns = [
    path("api/registrations/<uuid:registration_id>/story-access/", views.story_access, name="story-access"),
    path("api/remove-background/", views.remove_photo_background, name="remove-background"),
    path("api/montages/<uuid:montage_id>/backup/", views.backup_montage, name="montage-backup"),
    path("api/montages/<uuid:montage_id>/download/", views.deliver_download, name="montage-download"),
]
