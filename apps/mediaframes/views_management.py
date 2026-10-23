"""Internal artwork tools. No team photos, logos or exports are stored on the server."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.permissions import management_required
from .team_art import confirmed_teams, team_art_config


@never_cache
@management_required
@require_GET
def team_frame_editor(request, event_id):
    event = request.management_event
    teams = confirmed_teams(event)
    return render(request, "gestao/mediaframes/team_editor.html", {
        "event": event,
        "teams": teams,
        "team_art_config": team_art_config(event, teams),
    })
