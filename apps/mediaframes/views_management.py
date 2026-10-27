"""Internal artwork tools. No team photos, logos or exports are stored on the server."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.core.permissions import management_required
from .team_art import confirmed_teams
from .events_config import get_event_versions, select_version


@never_cache
@management_required
@require_GET
def official_frame_preview(request, event_id):
    """Create staff artwork locally without changing public paid-pilot access."""
    return _official_editor(request)


def _official_editor(request, team=False):
    event = request.management_event
    versions = get_event_versions(event)
    response = render(request, "events/frames.html", {
        "event": event, "event_versions": versions,
        "selected_version": select_version(request.GET.get("versao"), versions),
        "selected_format": "feed" if request.GET.get("formato") == "feed" else "story",
        "internal_editor": True,
        "artwork_subject": "team" if team or request.GET.get("tipo") == "equipe" else "pilot",
        "confirmed_teams": confirmed_teams(event),
        "studio_base_template": "gestao/base_gestao.html",
    })
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@never_cache
@management_required
@require_GET
def team_frame_editor(request, event_id):
    # Existing team-editor bookmarks use the same official theme as the catalog.
    return _official_editor(request, team=True)
