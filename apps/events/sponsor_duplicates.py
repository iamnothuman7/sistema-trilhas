"""Prevent repeated brand submissions without merging existing sponsorships."""
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import F

from apps.core.models import Organization
from .models import Sponsor, SponsorProfile
from .models_sponsors import sponsor_name_key


def require_unique_sponsor_name(*, organization_id, name):
    """Call inside the same transaction that creates the brand/participation.

    The write lock serializes name checks and inserts on SQLite as well as
    databases with row locks. Existing records, including archived ones, stay
    intact: the operator must choose to link, edit or restore them explicitly.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Sponsor duplicate validation requires an atomic creation transaction.")
    Organization.objects.filter(pk=organization_id).update(name=F("name"))
    Organization.objects.select_for_update().get(pk=organization_id)
    key = sponsor_name_key(name)
    profiles = SponsorProfile.objects.filter(organization_id=organization_id).only("name", "archived_at")
    legacy = Sponsor.objects.filter(event__organization_id=organization_id, profile__isnull=True).only("name", "archived_at")
    matches = [row for rows in (profiles, legacy) for row in rows if sponsor_name_key(row.name) == key]
    if not matches:
        return
    if all(row.archived_at for row in matches):
        message = "Esta marca já está nos cadastros excluídos. Restaure o cadastro existente antes de vinculá-lo ao evento."
    else:
        message = "Esta marca já está cadastrada. Edite o cadastro existente ou use Vincular marca para adicioná-la ao evento."
    raise ValidationError({"name": message})
