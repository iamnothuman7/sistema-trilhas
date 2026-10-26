"""Internal artwork tools. No team photos, logos or exports are stored on the server."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.permissions import management_required
from .team_art import confirmed_teams, team_art_config
from .events_config import get_event_versions, select_version


@never_cache
@management_required
@require_GET
def official_frame_preview(request, event_id):
    """Inspect the participant's actual renderer without granting paid-pilot access."""
    event = request.management_event
    versions = get_event_versions(event)
    response = render(request, "events/frames.html", {
        "event": event, "event_versions": versions,
        "selected_version": select_version(request.GET.get("versao"), versions),
        "selected_format": "feed" if request.GET.get("formato") == "feed" else "story",
        "internal_preview": True, "studio_base_template": "gestao/base_gestao.html",
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


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
