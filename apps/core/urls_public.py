from django.urls import path
from . import views_public

app_name = "core"

urlpatterns = [
    path("", views_public.home, name="home"),
    path("sobre/", views_public.about, name="about"),
    path("contato/", views_public.contact, name="contact"),
    path("privacidade/", views_public.privacy, name="privacy"),
]
