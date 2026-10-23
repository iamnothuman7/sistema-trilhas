"""Private first access using the original authorized checkout session, never email alone."""
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.forms import SetPasswordForm
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from apps.checkout.models import Order, OrderStatus
from apps.checkout.views import authorized_order, private_checkout
from apps.core.utils import log_audit
from .models import CheckoutAccount, User
from .onboarding import provision_buyer_account


@private_checkout
@sensitive_post_parameters("new_password1", "new_password2", "password")
@require_http_methods(["GET", "POST"])
def purchase_access(request, order_id):
    order = authorized_order(request, order_id)
    if order.status != OrderStatus.PAID:
        messages.warning(request, "O acesso da compra fica disponível após a confirmação do pagamento.")
        return redirect("checkout:order_result", order_id=order.pk)
    if request.user.is_authenticated and order.purchaser_user_id == request.user.pk:
        return redirect("minha_conta:dashboard")
    # POST supports legacy paid orders too. GET never creates an account or logs in.
    if request.method == "POST":
        created = provision_buyer_account(order)
        order.refresh_from_db()
        if created:
            return redirect("identity:purchase_access", order_id=order.pk)
    origin = CheckoutAccount.objects.select_related("user").filter(order=order).first()
    new_account = bool(origin and not origin.activated_at and not origin.user.has_usable_password())
    context = {"order": order, "new_account": new_account, "needs_prepare": not origin and not order.purchaser_user_id}
    if request.user.is_authenticated:
        context["access_error"] = "Você está conectado com outra conta. Saia dela antes de acessar esta compra."
        return render(request, "identity/purchase_access.html", context, status=409)
    if request.method == "GET":
        if new_account:
            context["form"] = SetPasswordForm(origin.user)
        return render(request, "identity/purchase_access.html", context)
    if request.POST.get("prepare"):
        return redirect("identity:purchase_access", order_id=order.pk)
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status != OrderStatus.PAID:
            return redirect("checkout:order_result", order_id=locked.pk)
        if new_account:
            origin = CheckoutAccount.objects.select_for_update().get(pk=origin.pk)
            user = User.objects.select_for_update().get(pk=origin.user_id)
            if origin.activated_at or user.has_usable_password() or not user.is_active or user.is_staff or user.is_superuser or user.event_memberships.exists():
                messages.error(request, "Este primeiro acesso não está mais disponível. Use seu login habitual.")
                return redirect("identity:purchase_access", order_id=order.pk)
            form = SetPasswordForm(user, request.POST)
            if not form.is_valid():
                context["form"] = form
                return render(request, "identity/purchase_access.html", context, status=400)
            user = form.save()
            origin.activated_at = timezone.now()
            origin.save(update_fields=["activated_at"])
        else:
            # The checkout session plus the existing password prove both sides.
            user = authenticate(request, email=locked.purchaser_email.lower().strip(), password=request.POST.get("password", ""))
            if user is None or (locked.purchaser_user_id and locked.purchaser_user_id != user.pk):
                context["access_error"] = "Senha incorreta ou conta indisponível. Confira seu acesso ou fale com a organização."
                return render(request, "identity/purchase_access.html", context, status=400)
            locked.purchaser_user = user
            locked.save(update_fields=["purchaser_user", "updated_at"])
        log_audit(actor=user, event_id=order.event_id, action="CHECKOUT_ACCOUNT_ACTIVATED" if new_account else "CHECKOUT_ACCOUNT_LINKED",
                  entity_type="Order", entity_id=order.pk, request=request)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    messages.success(request, "Sua conta está pronta. Acompanhe suas inscrições e conclua os termos de cada participante.")
    return redirect("minha_conta:dashboard")
