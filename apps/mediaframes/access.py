"""Paid pilot artwork access, independent of legal acceptance for event admission."""
from apps.catalog.models import ProductKind
from apps.checkout.models import OrderStatus
from apps.events.models import EventStatus
from apps.registrations.models import CommercialState, Registration
from apps.registrations.views_minha_conta import visible_registrations


def paid_pilot_registrations(user, *, event=None):
    if not user.is_authenticated or not user.is_active:
        return Registration.objects.none()
    registrations = visible_registrations(user).filter(
        commercial_state=CommercialState.ACTIVE,
        source_order_line__order__status=OrderStatus.PAID,
    ).exclude(event__status__in=[EventStatus.DRAFT, EventStatus.CANCELED, EventStatus.ARCHIVED])
    if event is not None:
        registrations = registrations.filter(event=event)
    return registrations.select_related("person", "event", "source_order_line__product")


def is_pilot(reg):
    # Purchase snapshots take precedence over later catalog edits.
    snapshot = reg.source_order_line.item_snapshot_json or {}
    kind = snapshot.get("kind") or (reg.source_order_line.product.kind if reg.source_order_line.product else "")
    return kind == ProductKind.PILOTO


def available_pilot_art(user, *, event=None):
    return [reg for reg in paid_pilot_registrations(user, event=event) if is_pilot(reg)]


def authorized_pilot_art(user, registration_id):
    from django.core.exceptions import ValidationError
    try:
        reg = paid_pilot_registrations(user).filter(pk=registration_id).first()
    except (ValueError, TypeError, ValidationError):
        return None
    return reg if reg and is_pilot(reg) else None
