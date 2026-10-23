"""Event-scoped, aggregate-only rankings of paid and active registrations."""
import unicodedata

from django.core.exceptions import ValidationError
from django.db.models import Count

from apps.checkout.models import OrderStatus
from apps.registrations.models import CommercialState, Registration


def group_key(value):
    """Ignore accents, letter case and whitespace, but never guess similar names."""
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(character for character in text if not unicodedata.combining(character) and not character.isspace())


def display_name(value):
    return " ".join(str(value or "").split())


MISSING_KEYS = {"", "naoinformado", "naoinformada", "semequipe", "semcidade", "-"}
STATE_CODES = tuple("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())


def alias_model(kind):
    from .models import CityAlias, TeamAlias
    return CityAlias if kind == "city" else TeamAlias


def alias_map(event, kind, state=None, exclude_pk=None):
    aliases = alias_model(kind).objects.filter(event=event)
    if kind == "city" and state is not None:
        aliases = aliases.filter(state=state)
    if exclude_pk:
        aliases = aliases.exclude(pk=exclude_pk)
    return {((alias.state if kind == "city" else ""), alias.source_key): alias.canonical_name for alias in aliases}


def validate_alias_mapping(event, kind, source_key, canonical_name, state="", exclude_pk=None):
    target_key = group_key(canonical_name)
    if source_key in MISSING_KEYS or target_key in MISSING_KEYS:
        raise ValidationError("Informe um nome real de equipe ou cidade.")
    mappings = alias_map(event, kind, state=state, exclude_pk=exclude_pk)
    mappings[(state, source_key)] = canonical_name
    for start_state, start_key in mappings:
        seen = set()
        key = start_key
        while (start_state, key) in mappings:
            if key in seen:
                raise ValidationError("Esta correção cria um ciclo entre nomes. Escolha o nome final que deve aparecer no ranking.")
            seen.add(key)
            following = group_key(mappings[(start_state, key)])
            if following == key:
                break  # A same-key alias can correct capitalization and accents.
            key = following


def _resolve(name, state, mappings):
    key = group_key(name)
    seen = set()
    while (state, key) in mappings and key not in seen:
        seen.add(key)
        name = mappings[(state, key)]
        following = group_key(name)
        if following == key:
            break
        key = following
    return key, name


def confirmed_registration_groups(event):
    """Each registration is counted once, including commercially paid records awaiting terms."""
    return list(
        Registration.objects.filter(
            event=event,
            commercial_state=CommercialState.ACTIVE,
            source_order_line__order__event=event,
            source_order_line__order__status=OrderStatus.PAID,
        )
        .values("team_name", "person__city", "person__state")
        .annotate(count=Count("pk", distinct=True))
        .order_by("team_name", "person__city", "person__state")
    )


def _aggregate(rows, kind, mappings):
    groups = {}
    for row in rows:
        name = display_name(row["person__city"] if kind == "city" else row["team_name"])
        state = display_name(row["person__state"]).upper() if kind == "city" else ""
        if group_key(name) in MISSING_KEYS:
            continue
        key, label = _resolve(name, state, mappings)
        identifier = (state, key)
        if identifier not in groups:
            groups[identifier] = {"name": label, "state": state, "count": 0}
        groups[identifier]["count"] += row["count"]
        # An explicit correction owns the display label, including same-key corrections.
        if (state, group_key(name)) in mappings or (state, key) in mappings:
            groups[identifier]["name"] = label
    ordered = sorted(groups.values(), key=lambda item: (-item["count"], group_key(item["name"]), item["state"]))
    previous_count, position = None, 0
    for index, item in enumerate(ordered, 1):
        if item["count"] != previous_count:
            position = index
        item["rank"] = position
        previous_count = item["count"]
    return ordered


def get_event_rankings(event, limit=10):
    rows = confirmed_registration_groups(event)
    teams = _aggregate(rows, "team", alias_map(event, "team"))
    cities = _aggregate(rows, "city", alias_map(event, "city"))
    result = {
        "confirmed_count": sum(row["count"] for row in rows),
        "team_group_count": len(teams),
        "city_group_count": len(cities),
        "team_registration_count": sum(team["count"] for team in teams),
        "city_registration_count": sum(city["count"] for city in cities),
        "teams": teams,
        "cities": cities,
    }
    if limit is not None:
        size = max(0, int(limit))
        result["teams"] = teams[:size]
        result["cities"] = cities[:size]
    return result
