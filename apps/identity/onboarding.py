"""Create the buyer's account after payment without claiming pre-existing identities."""
from django.db import transaction
from apps.checkout.models import Order, OrderStatus
from .models import CheckoutAccount, User


@transaction.atomic
def provision_buyer_account(order):
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != OrderStatus.PAID or locked.purchaser_user_id:
        return None
    email = locked.purchaser_email.strip().lower()
    # An email typed into checkout is not authentication to an existing account.
    user, created = User.objects.get_or_create(email=email, defaults={
        "full_name": locked.purchaser_name, "phone": locked.purchaser_phone,
    })
    if not created:
        return None
    user.set_unusable_password()
    user.save(update_fields=["password"])
    origin = CheckoutAccount.objects.create(order=locked, user=user)
    locked.purchaser_user = user
    locked.save(update_fields=["purchaser_user", "updated_at"])
    order.purchaser_user = user
    return origin
