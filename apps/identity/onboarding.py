"""Create the buyer's account after payment without claiming pre-existing identities."""
from django.db import transaction
from apps.checkout.models import Order, OrderStatus
from .models import CheckoutAccount, User


@transaction.atomic
def link_buyer_registration(order, user):
    """Honor the buyer's explicit checkout choice after their account is authenticated."""
    import re
    from .models import UserPersonLink, Person
    from apps.registrations.models import Registration, CommercialState
    if (not user.is_active or not user.has_usable_password() or order.purchaser_user_id != user.pk
            or order.status != OrderStatus.PAID):
        return
    cpf = re.sub(r"\D", "", order.purchaser_document or "")
    for reg in Registration.objects.select_for_update().filter(source_order_line__order=order,
            commercial_state=CommercialState.ACTIVE, participant_cpf=cpf):
        if not reg.product_snapshot_json.get("buyer_is_participant"):
            continue
        Person.objects.select_for_update().get(pk=reg.person_id)
        if not UserPersonLink.objects.filter(person_id=reg.person_id).exclude(user=user).exists():
            UserPersonLink.objects.get_or_create(user=user, person_id=reg.person_id,
                defaults={"method": "AUTHENTICATED_BUYER_CHECKOUT"})


@transaction.atomic
def provision_buyer_account(order):
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != OrderStatus.PAID:
        return None
    if locked.purchaser_user_id:
        link_buyer_registration(locked, locked.purchaser_user)
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
