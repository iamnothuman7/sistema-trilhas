"""The public page and checkout share the same currently available lots."""
from django.db.models import F
from django.utils import timezone

from .models import CapacityResource, CapacityResourceType, Lot, Product


def available_registration_lots(event):
    now = timezone.now()
    return Lot.objects.filter(
        event=event, product__event=event, product__active=True, active=True,
        opens_at__lte=now, closes_at__gte=now, current_sold__lt=F("max_quantity"),
    ).select_related("product").order_by("priority", "opens_at", "pk")


def registration_catalog(event):
    lots = list(available_registration_lots(event))
    capacities = {resource.reference_id: resource for resource in CapacityResource.objects.filter(
        event=event, type=CapacityResourceType.LOT_TOTAL, reference_id__in=[str(lot.pk) for lot in lots],
    )}
    # A pending checkout does not sell or block a seat. Reads remain read-only.
    lots = [lot for lot in lots if str(lot.pk) not in capacities or (
        lot.max_quantity > max(lot.current_sold, capacities[str(lot.pk)].confirmed_occupancy)
    )]
    products = list(Product.objects.filter(pk__in={lot.product_id for lot in lots}).prefetch_related("variants"))
    offers = [{"product": product, "lot": next(lot for lot in lots if lot.product_id == product.pk)} for product in products]
    return products, lots, offers
