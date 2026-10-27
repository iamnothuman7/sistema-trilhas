"""
Modelos visuais de molduras 3D. Os dados públicos vêm sempre da edição atual.
Compartilhado entre o backend Django e o renderizador Canvas 2D.
"""
from copy import deepcopy
from mimetypes import guess_type
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.templatetags.static import static
from django.utils import timezone
from django.utils.text import slugify

DEFAULT_VERSION = "trilhao-fogo"

BASE_LAYOUT = {
    "kind": "event",
    "photo": {"x": 130, "y": 620, "width": 820, "height": 765},
    "pop_out": 105,
    "banner_y": 1358,
    "credit_y": 1605,
    "footer_y": 1680,
    "logo": {"x": 150, "y": 80, "width": 780, "height": 420},
    "footer_columns": {
        "date_x": 220,
        "concentration_x": 540,
        "departure_x": 860,
        "dividers": [380, 700],
    },
}

# Estes modelos contêm somente estilo: nenhuma data, cidade ou edição fixa.
EVENT_VERSIONS = [
    {
        "id": "trilhao-fogo",
        "label": "Fogo & Terra (Oficial)",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {"yellow": "#ff9d00", "blue": "#1a0602", "green": "#ea580c", "navy": "#140502"},
        "background_colors": ["#c22506", "#5e0c02", "#160401"],
        "decorations": {
            "kind": "party", "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None, "tone": "#3d0802",
        },
    },
    {
        "id": "trilhao-carbon",
        "label": "Dark Carbon",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {"yellow": "#f59e0b", "blue": "#090d16", "green": "#10b981", "navy": "#070b12"},
        "background_colors": ["#1e293b", "#0f172a", "#05070c"],
        "decorations": {
            "kind": "party", "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None, "tone": "#09101d",
        },
    },
    {
        "id": "trilhao-amber",
        "label": "Laranja Racing",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {"yellow": "#ffdd12", "blue": "#1c0902", "green": "#ea580c", "navy": "#260b02"},
        "background_colors": ["#f97316", "#c2410c", "#431407"],
        "decorations": {
            "kind": "party", "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None, "tone": "#7c2d12",
        },
    },
]

MONTHS = ("JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
          "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
WEEKDAYS = ("SEGUNDA-FEIRA", "TERÇA-FEIRA", "QUARTA-FEIRA", "QUINTA-FEIRA",
            "SEXTA-FEIRA", "SÁBADO", "DOMINGO")


def _local_datetime(value, event):
    if value is None:
        return None
    try:
        event_zone = ZoneInfo(getattr(event, "timezone", None) or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        event_zone = timezone.get_default_timezone()
    if timezone.is_naive(value):
        return timezone.make_aware(value, event_zone)
    return timezone.localtime(value, event_zone)


def _asset_url(asset):
    if not asset:
        return None
    try:
        return asset.url
    except (ValueError, AttributeError):
        return None


def get_event_versions(event=None):
    versions = deepcopy(EVENT_VERSIONS)
    title = getattr(event, "title", None) or "Evento Equipe Morcego"
    city = getattr(event, "city", None) or ""
    state = getattr(event, "state", None) or ""
    city_state = " / ".join(part for part in (city, state) if part)
    venue = getattr(event, "venue", None) or ""
    location = " · ".join(part for part in (venue, city_state) if part) or "Local a confirmar"
    theme = getattr(event, "theme", None)
    logo_url = _asset_url(getattr(theme, "logo_image", None)) or static("img/logo_emblem.png")
    is_third_trilhao = getattr(event, "subdomain", None) == "3trilhaotiangua"
    if is_third_trilhao:
        logo_url = static("img/logo_3trilhao_2027.png")
    social_url = getattr(theme, "cover_url", "") or (static("img/events/3trilhao-cover-16x9.jpg") if is_third_trilhao else logo_url)
    start = _local_datetime(getattr(event, "starts_at", None), event)
    end = _local_datetime(getattr(event, "ends_at", None), event)

    date_short, date_long, day, month, weekday, year = ("A DEFINIR", "Data a confirmar", "—", "", "DATA", "")
    if start:
        day = f"{start.day:02d}"
        month = MONTHS[start.month - 1]
        year = str(start.year)
        weekday = WEEKDAYS[start.weekday()]
        date_short = f"{day} {month[:3]}"
        date_long = f"{start.day} de {month.lower()} de {start.year}"
        if end and end.date() != start.date():
            end_month = MONTHS[end.month - 1]
            if (start.year, start.month) == (end.year, end.month):
                day = f"{start.day:02d}–{end.day:02d}"
                date_short = f"{day} {month[:3]}"
                date_long = f"{start.day} a {end.day} de {month.lower()} de {start.year}"
            else:
                date_short = f"{start:%d/%m}–{end:%d/%m}"
                date_long += f" a {end.day} de {end_month.lower()} de {end.year}"
            if start.year != end.year:
                year = f"{start.year}–{end.year}"
            weekday = year
        else:
            weekday = f"{weekday} · {year}"

    start_time = start.strftime("%HH%M") if start else "—"
    end_time = end.strftime("%HH%M") if end else "—"
    edition = getattr(event, "edition_number", None)
    description = getattr(event, "description", None) or f"{title}. {date_long}. {location}."
    event_slug = getattr(event, "slug", None) or slugify(title) or "equipe-morcego"
    details = {
        "title": title,
        "selector_detail": f"{date_short} · {year}".strip(" ·"),
        "day": day,
        "month": month,
        "date_short": date_short,
        "weekday": weekday,
        "time_label": f"ÀS {start_time}" if start else "Horário a confirmar",
        # As chaves antigas são mantidas para compatibilidade com o renderizador.
        "concentration_time": start_time,
        "departure_time": end_time,
        "start_label": "INÍCIO",
        "end_label": "TÉRMINO",
        "header_time": year,
        "description": description,
        "ticket_title": f"{date_long} · Início {start_time} · Término {end_time}",
        "ticket_subtitle": location,
        "footer_label": "INÍCIO",
        "footer_title": "EQUIPE MORCEGO",
        "footer_subtitle": city_state.upper() or "LOCAL A CONFIRMAR",
        "reference_artwork": False,
        "top_label": f"EDIÇÃO {edition}" if edition else "EQUIPE MORCEGO",
        "attendance": "EU VOU!",
        "logo": logo_url,
        "social_image": social_url,
        "social_image_url": social_url,
        "social_width": None,
        "social_height": None,
        "social_type": guess_type(urlsplit(social_url).path)[0] or "image/png",
    }
    for version in versions:
        version.update(details)
        if is_third_trilhao:
            version["background_art"] = static("img/frames/trilhao-terra-story-v1.webp")
            version["decorations"] = {"kind": "offroad", "crowd": None}
            version["layout"]["logo"] = {"x": 190, "y": 90, "width": 700, "height": 410}
            version["footer_title"] = venue.upper() or "LOCAL A CONFIRMAR"
            if version["id"] == DEFAULT_VERSION:
                version["label"] = "Terra & Trilha · 3º Trilhão"
        version["feed_layout"] = {
            **deepcopy(version["layout"]),
            "logo": {"x": 265, "y": 65, "width": 550, "height": 250},
            "photo": {"x": 130, "y": 400, "width": 820, "height": 550},
            "pop_out": 65, "banner_y": 940, "banner_scale": 0.78,
            "stamp_y": 1030, "stamp_height": 54, "stamp_baseline": 1067,
            "credit_y": 1102, "footer_y": 1120,
        }
        version["filename"] = f"eu-vou-{event_slug}-{version['id']}.png"
    return versions


def select_version(identifier, versions):
    return next(
        (version for version in versions if version["id"] == identifier),
        versions[0],
    )
