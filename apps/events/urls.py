from django.urls import path
from . import views
from . import views_raffles

app_name = "events"

urlpatterns = [
    path("eventos/", views.event_list, name="list"),
    path("eventos/<slug:slug>/", views.event_detail, name="detail"),
    path("eventos/<slug:slug>/programacao/", views.event_schedule, name="schedule"),
    path("eventos/<slug:slug>/percurso/", views.event_route, name="route"),
    path("eventos/<slug:slug>/regulamento/", views.event_rules, name="rules"),
    path("eventos/<slug:slug>/patrocinadores/", views.event_sponsors, name="sponsors"),
    path("eventos/<slug:slug>/molduras/", views.event_frames, name="frames"),
    path("eventos/<slug:slug>/sorteio/", views_raffles.raffle_detail, name="raffle"),
    path("eventos/<slug:slug>/sorteio/interesse/", views_raffles.raffle_interest, name="raffle_interest"),
]
