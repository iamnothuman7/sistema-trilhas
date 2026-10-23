"""Private email recovery, including unactivated accounts created by paid checkout."""
import logging
import smtplib
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN, PasswordResetConfirmView
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods

from apps.checkout.models import Order, OrderStatus
from apps.core.utils import log_audit
from apps.registrations.views_minha_conta import private_page
from .models import CheckoutAccount, User

logger = logging.getLogger(__name__)


def _can_recover(user, origin=None):
    if not user.is_active:
        return False
    if user.has_usable_password():
        return True
    origin = origin or getattr(user, "checkout_origin", None)
    return bool(origin and not origin.activated_at and origin.order.status == OrderStatus.PAID
                and not user.is_staff and not user.is_superuser
                and not user.event_memberships.exists())


def recovery_configuration():
    """A fixed origin prevents Host-header injection; no console/file email fallback."""
    try:
        origin = urlsplit(getattr(settings, "PUBLIC_SITE_URL", ""))
        origin_ok = bool(origin.hostname and not origin.username and not origin.password
                         and not origin.query and not origin.fragment and origin.path in ("", "/")
                         and (origin.scheme == "https" or (settings.DEBUG and origin.scheme == "http"
                              and origin.hostname in ("localhost", "127.0.0.1", "testserver"))))
        origin.port  # Reject malformed configured ports before producing any message.
    except ValueError:
        return None
    backend = settings.EMAIL_BACKEND
    smtp = backend == "django.core.mail.backends.smtp.EmailBackend" and bool(getattr(settings, "EMAIL_HOST", ""))
    if smtp and getattr(settings, "EMAIL_USE_TLS", False) and getattr(settings, "EMAIL_USE_SSL", False):
        return None
    local_test = settings.DEBUG and backend == "django.core.mail.backends.locmem.EmailBackend"
    if not origin_ok or not getattr(settings, "DEFAULT_FROM_EMAIL", "") or not (smtp or local_test):
        return None
    return origin


def _return_to_purchase(request):
    identifiers = []
    for value in request.session.get("checkout_order_ids", [])[:100]:
        try:
            identifiers.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    order = Order.objects.filter(pk__in=identifiers, status=OrderStatus.PAID).order_by("-created_at").first()
    return reverse("identity:purchase_access", kwargs={"order_id": order.pk}) if order else ""


class RecoveryForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update({"autocomplete": "email", "placeholder": "Seu e-mail cadastrado", "autocapitalize": "none"})

    def get_users(self, email):
        users = User.objects.filter(email__iexact=email.strip(), is_active=True).select_related("checkout_origin__order")
        return (user for user in users if _can_recover(user))

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None):
        # Django's default logs exceptions with user data. Handle failures once, without tokens/PII.
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        EmailMultiAlternatives(subject, body, from_email, [to_email]).send(fail_silently=False)


@private_page
@csrf_protect
@sensitive_post_parameters("email")
@require_http_methods(["GET", "POST"])
def reset_request(request):
    origin = recovery_configuration()
    form = RecoveryForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and origin and form.is_valid():
        fingerprint = salted_hmac("password-reset-throttle", form.cleaned_data["email"].strip().casefold()).hexdigest()
        # Response does not reveal whether an address exists or is being throttled.
        if cache.add("password-reset:" + fingerprint, True, timeout=300):
            try:
                form.save(domain_override=origin.netloc, use_https=origin.scheme == "https",
                          from_email=settings.DEFAULT_FROM_EMAIL, request=request,
                          subject_template_name="identity/password_reset_subject.txt",
                          email_template_name="identity/password_reset_email.txt")
            except (OSError, smtplib.SMTPException):
                logger.warning("Password recovery email delivery unavailable.")
        return redirect("identity:password_reset_done")
    return render(request, "identity/password_reset.html", {
        "form": form, "recovery_available": bool(origin), "return_to_purchase": _return_to_purchase(request),
    }, status=400 if request.method == "POST" and origin else 200)


@private_page
@require_GET
def reset_done(request):
    return render(request, "identity/password_reset_done.html", {
        "recovery_available": bool(recovery_configuration()), "return_to_purchase": _return_to_purchase(request),
    })


@method_decorator(private_page, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class RecoveryConfirmView(PasswordResetConfirmView):
    template_name = "identity/password_reset_confirm.html"
    success_url = reverse_lazy("identity:password_reset_complete")
    http_method_names = ["get", "post", "head", "options"]

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        return user if user and _can_recover(user) else None

    def form_valid(self, form):
        # Recheck the token under locks: two tabs must not both replace a password.
        # Order -> origin -> user matches checkout and keeps initial activation paid.
        with transaction.atomic():
            origin_reference = CheckoutAccount.objects.filter(user=self.user).first()
            locked_order = Order.objects.select_for_update().get(pk=origin_reference.order_id) if origin_reference else None
            origin = CheckoutAccount.objects.select_for_update().filter(user=self.user).first()
            if origin and locked_order:
                origin.order = locked_order
            user = User.objects.select_for_update().get(pk=self.user.pk)
            token = self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)
            if not _can_recover(user, origin) or not self.token_generator.check_token(user, token):
                self.validlink = False
                return self.render_to_response(self.get_context_data(), status=400)
            locked_form = SetPasswordForm(user, self.request.POST)
            if not locked_form.is_valid():
                return self.form_invalid(locked_form)
            locked_form.save()
            if origin and not origin.activated_at:
                origin.activated_at = timezone.now()
                origin.save(update_fields=["activated_at"])
            log_audit(actor=user, action="PASSWORD_RESET", entity_type="User", entity_id=user.pk)
        self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
        # Explicit login after recovery avoids confusing a different logged-in account.
        return redirect(self.success_url)


reset_confirm = RecoveryConfirmView.as_view()


@private_page
@require_GET
def reset_complete(request):
    return render(request, "identity/password_reset_complete.html")
