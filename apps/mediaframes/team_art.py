"""Read-only configuration for the staff-only team artwork editor."""
from hashlib import sha256

from django.templatetags.static import static

from apps.events.rankings import get_event_rankings, group_key
from .events_config import get_event_versions


def confirmed_teams(event):
    """Use the same paid-active scope and explicit aliases as the event ranking."""
    return sorted([
        {"id": sha256(group_key(row["name"]).encode()).hexdigest()[:20], "name": row["name"], "count": row["count"]}
        for row in get_event_rankings(event, limit=None)["teams"]
    ], key=lambda team: team["name"].casefold())


def team_art_config(event, teams):
    details = get_event_versions(event)[0]
    theme = getattr(event, "theme", None)
    uploaded_logo = getattr(theme, "logo_image", None)
    if uploaded_logo:
        logo_url = uploaded_logo.url
    elif event.slug == "3-trilhao-tiangua-off-road-2027":
        logo_url = static("img/logo_3trilhao_2027.png")
    else:
        logo_url = static("img/logo_emblem.png")
    return {
        "eventTitle": event.title,
        "eventSlug": event.slug,
        "dateLabel": details["selector_detail"],
        "location": " / ".join(part for part in (event.city, event.state) if part),
        "editionLogo": logo_url,
        "brandLogo": static("img/logo_emblem.png"),
        "teams": teams,
        "maxUploadBytes": 15 * 1024 * 1024,
        "maxImagePixels": 24_000_000,
    }
