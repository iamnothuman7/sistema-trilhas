"""
Configuração das edições e versões de molduras 3D Pop-Out (padrão Flávio Folia / NaBio).
Compartilhado entre o backend Django e o renderizador Canvas 2D.
"""
from copy import deepcopy

DEFAULT_VERSION = "trilhao-fogo"

BASE_LAYOUT = {
    "kind": "event",
    "photo": {"x": 185, "y": 705, "width": 710, "height": 680},
    "pop_out": 105,
    "banner_y": 1358,
    "credit_y": 1605,
    "footer_y": 1680,
    "logo": {"x": 60, "y": 80, "width": 960, "height": 580},
    "footer_columns": {
        "date_x": 220,
        "concentration_x": 540,
        "departure_x": 860,
        "dividers": [380, 700],
    },
}

EVENT_VERSIONS = [
    {
        "id": "trilhao-fogo",
        "label": "Fogo & Terra (Oficial)",
        "title": "3º Trilhão Tianguá Off Road 2027",
        "selector_detail": "20 E 21 MAR · OFICIAL",
        "day": "20 E 21",
        "month": "MARÇO",
        "date_short": "20-21 MAR",
        "weekday": "MARÇO 2027",
        "time_label": "ÀS 06H00",
        "concentration_time": "06H00",
        "departure_time": "08H00",
        "header_time": "2027",
        "description": "O maior encontro off-road da Serra da Ibiapaba já tem data marcada: 20 e 21 de março de 2027.",
        "ticket_title": "Concentração 06h00 · Largada 08h00",
        "ticket_subtitle": "Polo de Lazer · Tianguá / CE",
        "footer_label": "CONCENTRAÇÃO",
        "footer_title": "EQUIPE MORCEGO",
        "footer_subtitle": "TIANGUÁ · CEARÁ",
        "reference_artwork": False,
        "top_label": "3º TRILHÃO",
        "attendance": "EU VOU!",
        "logo": "/static/img/logo_3trilhao_2027.png",
        "social_image": "img/cartaz_trilhao_2027.jpg",
        "social_width": 819,
        "social_height": 1024,
        "social_type": "image/jpeg",
        "filename": "eu-vou-3-trilhao-tiangua-2027-fogo.png",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {
            "yellow": "#ff9d00",
            "blue": "#1a0602",
            "green": "#ea580c",
            "navy": "#140502",
        },
        "background_colors": ["#c22506", "#5e0c02", "#160401"],
        "decorations": {
            "kind": "party",
            "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None,
            "tone": "#3d0802",
        },
    },
    {
        "id": "trilhao-carbon",
        "label": "Dark Carbon",
        "title": "3º Trilhão Tianguá Off Road 2027 · Dark Carbon",
        "selector_detail": "20 E 21 MAR · NOTURNO",
        "day": "20 E 21",
        "month": "MARÇO",
        "date_short": "20-21 MAR",
        "weekday": "MARÇO 2027",
        "time_label": "ÀS 06H00",
        "concentration_time": "06H00",
        "departure_time": "08H00",
        "header_time": "2027",
        "description": "Edição Dark Carbon da Equipe Morcego para o 3º Trilhão Tianguá Off Road 2027.",
        "ticket_title": "Concentração 06h00 · Largada 08h00",
        "ticket_subtitle": "Polo de Lazer · Tianguá / CE",
        "footer_label": "CONCENTRAÇÃO",
        "footer_title": "EQUIPE MORCEGO",
        "footer_subtitle": "TIANGUÁ · CEARÁ",
        "reference_artwork": False,
        "top_label": "3º TRILHÃO",
        "attendance": "EU VOU!",
        "logo": "/static/img/logo_3trilhao_2027.png",
        "social_image": "img/cartaz_trilhao_2027.jpg",
        "social_width": 819,
        "social_height": 1024,
        "social_type": "image/jpeg",
        "filename": "eu-vou-3-trilhao-tiangua-2027-carbon.png",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {
            "yellow": "#f59e0b",
            "blue": "#090d16",
            "green": "#10b981",
            "navy": "#070b12",
        },
        "background_colors": ["#1e293b", "#0f172a", "#05070c"],
        "decorations": {
            "kind": "party",
            "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None,
            "tone": "#09101d",
        },
    },
    {
        "id": "trilhao-amber",
        "label": "Laranja Racing",
        "title": "3º Trilhão Tianguá Off Road 2027 · Laranja Racing",
        "selector_detail": "20 E 21 MAR · RACING",
        "day": "20 E 21",
        "month": "MARÇO",
        "date_short": "20-21 MAR",
        "weekday": "MARÇO 2027",
        "time_label": "ÀS 06H00",
        "concentration_time": "06H00",
        "departure_time": "08H00",
        "header_time": "2027",
        "description": "Edição Laranja Racing do 3º Trilhão Tianguá Off Road 2027.",
        "ticket_title": "Concentração 06h00 · Largada 08h00",
        "ticket_subtitle": "Polo de Lazer · Tianguá / CE",
        "footer_label": "CONCENTRAÇÃO",
        "footer_title": "EQUIPE MORCEGO",
        "footer_subtitle": "TIANGUÁ · CEARÁ",
        "reference_artwork": False,
        "top_label": "3º TRILHÃO",
        "attendance": "EU VOU!",
        "logo": "/static/img/logo_3trilhao_2027.png",
        "social_image": "img/cartaz_trilhao_2027.jpg",
        "social_width": 819,
        "social_height": 1024,
        "social_type": "image/jpeg",
        "filename": "eu-vou-3-trilhao-tiangua-2027-racing.png",
        "layout": deepcopy(BASE_LAYOUT),
        "palette": {
            "yellow": "#ffdd12",
            "blue": "#1c0902",
            "green": "#ea580c",
            "navy": "#260b02",
        },
        "background_colors": ["#f97316", "#c2410c", "#431407"],
        "decorations": {
            "kind": "party",
            "crowd": "/static/assets/party-crowd-silhouette-v1.webp",
            "flag": None,
            "tone": "#7c2d12",
        },
    },
]


def get_event_versions(event=None):
    versions = deepcopy(EVENT_VERSIONS)
    if event:
        logo_url = "/static/img/logo_3trilhao_2027.png"
        if hasattr(event, "theme") and event.theme and event.theme.logo_image:
            try:
                logo_url = event.theme.logo_image.url
            except Exception:
                pass
        for v in versions:
            v["logo"] = logo_url
            v["footer_subtitle"] = f"{event.city.upper()} · {event.state.upper()}"
    return versions


def select_version(identifier, versions):
    return next(
        (v for v in versions if v["id"] == identifier),
        versions[0],
    )
