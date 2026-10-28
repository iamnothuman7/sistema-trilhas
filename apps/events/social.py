"""Server-rendered link previews, also available to crawlers without JavaScript."""
from django.templatetags.static import static
from apps.mediaframes.events_config import get_event_versions


def institutional_social_metadata(request):
    """The team portal keeps its identity when an event's campaign changes."""
    return {
        "title": "Equipe Morcego | A vida é melhor na terra",
        "description": "Trilha, amizade e histórias na Serra da Ibiapaba. Conheça a Equipe Morcego, encontre o próximo evento e prepare-se para a largada.",
        "url": request.build_absolute_uri(request.path),
        "image": request.build_absolute_uri(static("img/trail-hero-desktop-v2.webp")),
        "image_type": "image/webp", "width": 1536, "height": 1024,
    }


def social_metadata(request, event, version=None):
    version = version or get_event_versions(event)[0]
    image = version["social_image_url"]
    width, height = None, None
    if image.endswith("img/events/3trilhao-cover-16x9.jpg"):
        # Dimensions of the shipped artwork. A new URL refreshes image caches.
        width, height = 1672, 941
        image += "?v=20261026-share2"
    return {
        "title": version["title"], "description": version["description"],
        "url": request.build_absolute_uri(request.path),
        "image": request.build_absolute_uri(image),
        "image_type": version["social_type"], "width": width, "height": height,
    }
