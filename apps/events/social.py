"""Server-rendered link previews, also available to crawlers without JavaScript."""
from apps.mediaframes.events_config import get_event_versions


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
