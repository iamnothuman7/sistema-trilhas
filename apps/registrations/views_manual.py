import hashlib
import json
import uuid
from datetime import timedelta
from functools import wraps
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from apps.checkout.models import OrderStatus
from apps.core.permissions import management_required, can_manage_event
from apps.core.utils import log_audit
from apps.payments.services import expire_stale_orders
from .forms_manual import ManualRegistrationForm, PaymentReceiptForm, PAYMENT_METHODS
from .manual import create_manual_registration, confirm_manual_receipt
from .models import ManualRegistration

FORM_SALT = "manual-registration-form-v1"
ACCESS_SALT = "manual-registration-access-v1"


def private_manual(view):
    @wraps(view)
    @never_cache
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response
    return wrapper


def _payload_hash(post):
    return hashlib.sha256(json.dumps(sorted((k, post.getlist(k)) for k in post if k not in {"csrfmiddlewaretoken", "request_token"}), ensure_ascii=False).encode()).hexdigest()


@private_manual
@management_required
@require_http_methods(["GET", "POST"])
def manual_registration_create(request, event_id):
    event = request.management_event
    can_receive = can_manage_event(request.user, event, "finance")
    token = signing.dumps({"key": str(uuid.uuid4()), "event": str(event.pk), "actor": str(request.user.pk)}, salt=FORM_SALT)
    form = ManualRegistrationForm(request.POST if request.method == "POST" else None, event=event, can_receive=can_receive,
        initial={"request_token": token, "city": event.city, "state": event.state, "shirt": "M", "payment_state": "pending"})
    if request.method == "POST":
        try:
            payload = signing.loads(request.POST.get("request_token", ""), salt=FORM_SALT, max_age=7200)
            if payload.get("actor") != str(request.user.pk) or payload.get("event") != str(event.pk):
                raise signing.BadSignature
            key = uuid.UUID(payload["key"])
        except (signing.BadSignature, ValueError, KeyError, TypeError, AttributeError):
            form.is_valid()
            form.add_error(None, "Este formulário expirou. Abra uma nova inscrição e tente novamente.")
        else:
            request_hash = _payload_hash(request.POST)
            existing = ManualRegistration.objects.filter(request_key=key, created_by=request.user, registration__event=event).first()
            if existing and existing.request_hash == request_hash:
                return redirect("gestao:registration_detail", event_id=event.pk, reg_id=existing.registration_id)
            if form.is_valid():
                try:
                    expire_stale_orders(event_id=event.pk)
                    entry = create_manual_registration(event=event, actor=request.user, data=form.cleaned_data,
                        request_key=key, request_hash=request_hash, request=request)
                except ValidationError as exc:
                    form.add_error(None, exc)
                except IntegrityError:
                    form.add_error(None, "O CPF já possui uma inscrição ou reserva, ou este formulário já foi enviado. Consulte a lista antes de tentar novamente.")
                except OperationalError:
                    form.add_error(None, "Outra operação está em andamento. Aguarde alguns segundos e tente novamente com este mesmo formulário.")
                else:
                    messages.success(request, "Inscrição manual cadastrada. Confira a ficha e entregue o link de acesso ao responsável.")
                    return redirect("gestao:registration_detail", event_id=event.pk, reg_id=entry.registration_id)
    catalog = {}
    for lot in form.fields["lot"].queryset.prefetch_related("product__variants"):
        variants = [v for v in lot.product.variants.all() if v.kind == "SHIRT_SIZE"]
        sizes = [{"code": v.code, "label": v.label, "extra": v.extra_price_cents} for v in variants] if variants else [{"code": s, "label": s, "extra": 0} for s in ("P", "M", "G", "GG", "XG")]
        if not lot.product.includes_shirt:
            sizes = [{"code": "", "label": "Pacote sem camiseta", "extra": 0}]
        catalog[str(lot.pk)] = {"price": lot.price_cents, "sizes": sizes}
    return render(request, "gestao/registrations/manual_form.html", {"event": event, "form": form, "can_receive": can_receive, "manual_catalog": catalog},
                  status=400 if request.method == "POST" else 200)


def manual_context(request, reg):
    entry = ManualRegistration.objects.select_related("created_by").filter(registration=reg).first()
    if entry is None:
        return {}
    link = ""
    if not entry.access_used_at and entry.access_expires_at > timezone.now():
        token = signing.dumps({"entry": entry.pk, "nonce": str(entry.access_nonce)}, salt=ACCESS_SALT)
        link = request.build_absolute_uri(reverse("checkout:manual_access", kwargs={"token": token}))
    order = reg.source_order_line.order
    evidences = list(order.manual_evidences.select_related("verified_by"))
    for evidence in evidences:
        evidence.method_label = dict(PAYMENT_METHODS).get(evidence.method, evidence.method)
    return {"manual_entry": entry, "manual_access_url": link,
        "manual_evidences": evidences,
        "can_confirm_manual": can_manage_event(request.user, reg.event, "finance") and order.status == OrderStatus.RESERVED and not order.is_expired() and not order.charge_attempts.exists()}


@private_manual
@management_required
@require_http_methods(["GET", "POST"])
def manual_registration_payment(request, event_id, reg_id):
    entry = get_object_or_404(ManualRegistration.objects.select_related("registration__source_order_line__order"), registration_id=reg_id, registration__event_id=event_id)
    order = entry.registration.source_order_line.order
    return_url = reverse("gestao:registration_detail", kwargs={"event_id": event_id, "reg_id": reg_id}) if can_manage_event(request.user, request.management_event, "registrations") else reverse("gestao:finance", kwargs={"event_id": event_id})
    form = PaymentReceiptForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            confirm_manual_receipt(entry=entry, actor=request.user, data=form.cleaned_data, request=request)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Recebimento confirmado e lançado no financeiro. O participante ainda precisa aceitar os termos para liberar o QR Code.")
            return redirect(return_url)
    return render(request, "gestao/registrations/manual_payment.html", {"event": request.management_event, "reg": entry.registration, "order": order, "form": form, "return_url": return_url}, status=400 if request.method == "POST" else 200)


@private_manual
@management_required
@require_POST
def manual_registration_renew_link(request, event_id, reg_id):
    with transaction.atomic():
        entry = get_object_or_404(ManualRegistration.objects.select_for_update(), registration_id=reg_id, registration__event_id=event_id)
        entry.access_nonce = uuid.uuid4()
        entry.access_expires_at = timezone.now()+timedelta(days=7)
        entry.access_used_at = None
        entry.save(update_fields=["access_nonce", "access_expires_at", "access_used_at"])
        log_audit(actor=request.user, event_id=event_id, action="MANUAL_ACCESS_LINK_RENEWED", entity_type="Registration", entity_id=reg_id, request=request)
    messages.success(request, "Novo link de acesso gerado. O link anterior foi invalidado; envie apenas ao responsável.")
    return redirect("gestao:registration_detail", event_id=event_id, reg_id=reg_id)


@private_manual
@require_http_methods(["GET", "POST"])
def manual_access(request, token):
    context = {}
    try:
        payload = signing.loads(token, salt=ACCESS_SALT, max_age=7*24*3600)
        if not isinstance(payload, dict) or not isinstance(payload.get("entry"), int):
            raise signing.BadSignature
    except (signing.BadSignature, ValueError, TypeError):
        return render(request, "registrations/manual_access.html", {"access_error": "Link inválido ou expirado. Solicite um novo link à organização."}, status=410)
    with transaction.atomic():
        entry = get_object_or_404(ManualRegistration.objects.select_for_update().select_related("registration__event", "registration__source_order_line__order"), pk=payload["entry"])
        order = entry.registration.source_order_line.order
        context["event"] = entry.registration.event
        session_orders = request.session.get("checkout_order_ids", [])
        if str(entry.access_nonce) == payload.get("nonce") and entry.access_used_at and isinstance(session_orders, list) and str(order.pk) in session_orders:
            target = "identity:purchase_access" if order.status == OrderStatus.PAID else "checkout:order_payment"
            return redirect(target, order_id=order.pk)
        if str(entry.access_nonce) != payload.get("nonce") or entry.access_expires_at <= timezone.now() or entry.access_used_at:
            context["access_error"] = "Este link expirou ou já foi utilizado. Acesse sua conta ou solicite um novo link à organização."
            return render(request, "registrations/manual_access.html", context, status=410)
        if request.method == "POST":
            ids = request.session.get("checkout_order_ids", [])
            request.session["checkout_order_ids"] = (ids[-49:] if isinstance(ids, list) else [])+[str(order.pk)]
            # Persist access before consuming the single-use link.
            request.session.save()
            entry.access_used_at = timezone.now()
            entry.save(update_fields=["access_used_at"])
            log_audit(event_id=entry.registration.event_id, action="MANUAL_ACCESS_LINK_USED", entity_type="Registration", entity_id=entry.registration_id, request=request)
            target = "identity:purchase_access" if order.status == OrderStatus.PAID else "checkout:order_payment"
            return redirect(target, order_id=order.pk)
    return render(request, "registrations/manual_access.html", context)
