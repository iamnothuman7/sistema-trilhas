import hashlib
from .raffle_results import eligible_tickets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.permissions import can_manage_event, management_required
from apps.core.utils import log_audit
from .forms_raffles import MotorcycleRaffleForm, RaffleInterestForm
from .models import Event, EventStatus
from .models_raffles import MotorcycleRaffle, RaffleInterest, RAFFLE_INTEREST_CONSENT
from .views import get_event_resilient
from .raffle_images import raffle_image_writes


INTEREST_EVENT_STATUSES = {EventStatus.PUBLISHED, EventStatus.SALES_CLOSED, EventStatus.IN_PROGRESS}


def _public_context(request, event, form=None):
    configured = MotorcycleRaffle.objects.filter(event=event).first()
    preview = bool(configured and not configured.is_public and can_manage_event(request.user, event, "events"))
    raffle = configured if configured and (configured.is_public or preview) else None
    collecting = bool(raffle and raffle.is_public and raffle.interest_enabled and event.status in INTEREST_EVENT_STATUSES)
    registered = bool(raffle and request.user.is_authenticated and RaffleInterest.objects.filter(raffle=raffle, user=request.user).exists())
    my_tickets=[]
    if raffle and request.user.is_authenticated:
        from django.db.models import Q
        from .raffle_results import eligible_tickets
        my_tickets=eligible_tickets(raffle).filter(Q(registration__person__user_links__user=request.user)|
            Q(beneficiary_registration__person__user_links__user=request.user)).select_related('registration').distinct()
    from apps.checkout.addons import eligible_registrations
    purchase_regs=eligible_registrations(request.user).filter(event=event) if request.user.is_authenticated and raffle and raffle.purchase_available else []
    from .raffle_sales import price_options
    public_winners = []
    result = getattr(raffle, "result", None) if raffle else None
    if result:
        for winner in result.winners:
            parts = (winner.get("name") or "").split()
            public_winners.append({"prize": winner["prize"], "number": winner["number"],
                                   "name": (parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")) if parts else "Ganhador"})
    return {"event": event, "raffle": raffle, "raffle_preview": preview, "price_options": price_options(raffle) if raffle else [],
            "public_winners": public_winners,
            "interest_open": collecting, "interest_registered": registered,
            "interest_form": form or RaffleInterestForm(), 'my_raffle_tickets':my_tickets,'raffle_purchase_registrations':purchase_regs}


@require_GET
def raffle_detail(request, slug):
    event = get_event_resilient(slug, request)
    return render(request, "events/raffle_detail.html", _public_context(request, event))


@require_POST
@login_required(login_url="identity:login")
def raffle_interest(request, slug):
    if not request.user.is_active:
        raise PermissionDenied
    event = get_event_resilient(slug, request)
    form = RaffleInterestForm(request.POST)
    with transaction.atomic():
        raffle = get_object_or_404(MotorcycleRaffle.objects.select_for_update(), event=event, is_public=True, interest_enabled=True)
        if event.status not in INTEREST_EVENT_STATUSES:
            raise PermissionDenied("O registro de interesse não está disponível para este evento.")
        if not form.is_valid():
            return render(request, "events/raffle_detail.html", _public_context(request, event, form), status=400)
        interest, created = RaffleInterest.objects.get_or_create(
            raffle=raffle, user=request.user, defaults={"consent_text": RAFFLE_INTEREST_CONSENT},
        )
        if created:
            log_audit(actor=request.user, event_id=event.pk, action="RAFFLE_INTEREST_REGISTERED",
                      entity_type="RaffleInterest", entity_id=interest.pk, request=request)
    messages.success(request, "Interesse registrado. Nenhuma cota foi comprada ou reservada." if created else "Seu interesse já está registrado. Nenhuma cota foi comprada ou reservada.")
    return redirect("events:raffle", slug=event.slug)


@management_required
@require_http_methods(["GET", "POST"])
def raffle_management(request, event_id):
    event = request.management_event
    try:
        event_timezone = ZoneInfo(event.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        event_timezone = timezone.get_current_timezone()
    with timezone.override(event_timezone):
        # Serializing configuration per event prevents concurrent first saves
        # from creating competing one-to-one raffle records.
        with transaction.atomic():
            if request.method == "POST":
                Event.objects.select_for_update().get(pk=event.pk)
            raffle = MotorcycleRaffle.objects.filter(event=event).first() or MotorcycleRaffle(event=event)
            with raffle_image_writes(raffle):
                return _manage_form(request,event,raffle)


def _manage_form(request,event,raffle):
    form = MotorcycleRaffleForm(request.POST if request.method == "POST" else None,
        request.FILES if request.method=='POST' else None, instance=raffle)
    if request.method == "POST" and form.is_valid():
        creating = raffle.pk is None or raffle._state.adding
        saved = form.save(commit=False)
        saved.event = event
        saved.save()
        if saved.registration_tickets_enabled:
            from apps.registrations.models import Registration
            from .raffle_operations import issue_registration_ticket
            for reg in Registration.objects.filter(event=event, commercial_state='ACTIVE',
                    source_order_line__order__status='PAID', raffle_ticket__isnull=True).select_related('source_order_line__order'):
                issue_registration_ticket(reg)
        log_audit(actor=request.user, event_id=event.pk,
                  action="RAFFLE_CREATED" if creating else "RAFFLE_UPDATED",
                  entity_type="MotorcycleRaffle", entity_id=saved.pk,
                  diff={"fields": list(form.changed_data)}, request=request)
        messages.success(request, "Configuração salva. Venda online de números extras ativada." if saved.purchase_available else "Configuração salva. Venda de números extras desativada ou em preparação.")
        return redirect("gestao:raffle_management", event_id=event.pk)
    interests = RaffleInterest.objects.filter(raffle=raffle).select_related("user") if not raffle._state.adding else RaffleInterest.objects.none()
    tickets=raffle.tickets.all() if not raffle._state.adding else None
    return render(request, "gestao/raffles/manage.html", {
        "event": event, "raffle": raffle, "form": form,
        "saved_raffle": not raffle._state.adding,
        "interest_count": interests.count(), "interests_page": Paginator(interests, 20).get_page(request.GET.get("page")),
        'digital_count':eligible_tickets(raffle).filter(source='REGISTRATION').count() if tickets is not None else 0,
        'paper_sold':eligible_tickets(raffle).filter(source='PAPER').count() if tickets is not None else 0,
    }, status=400 if request.method == "POST" else 200)


def _buy_context(event, raffle, **extra):
    from apps.payments.gateway import MercadoPagoGateway
    from .raffle_sales import price_options, price_cents, online_remaining, MAX_PER_SALE
    remaining = online_remaining(raffle)
    limit = min(MAX_PER_SALE, remaining) if remaining is not None else MAX_PER_SALE
    available = raffle.purchase_available and limit > 0
    table = {q: price_cents(raffle, q) for q in range(1, limit + 1)} if available else {}
    options = [o for o in price_options(raffle) if o["qty"] <= limit] if available else []
    best = max(options, key=lambda o: o["economy_cents"] / o["qty"], default=None)
    for option in options:
        option["popular"] = bool(best and best["qty"] > 1 and option is best)
    return {"event": event, "raffle": raffle, "combos": options, "price_table": table, "max_quantity": limit,
            "sales_open": available, "remaining": remaining,
            "payments_available": not MercadoPagoGateway().configuration_issues(), **extra}


@require_http_methods(["GET", "POST"])
def raffle_standalone_buy(request, slug):
    """Compra avulsa de números da sorte no site, para qualquer pessoa, com os pacotes configurados."""
    event = get_event_resilient(slug, request)
    raffle = get_object_or_404(MotorcycleRaffle, event=event, is_public=True)
    post = request.POST if request.method == "POST" else {}
    form_values = {"buyer_name": post.get("buyer_name", "").strip()[:150], "buyer_phone": post.get("buyer_phone", "").strip()[:30],
                   "buyer_email": post.get("buyer_email", "").strip().lower()[:254], "buyer_doc": post.get("buyer_document", "").strip()[:20]}
    try:
        quantity = int(post.get("quantity", request.GET.get("quantidade", "5")))
    except (ValueError, TypeError):
        quantity = 5
    context = _buy_context(event, raffle, **form_values)
    quantity = max(1, min(context["max_quantity"] or 1, quantity))
    context["quantity"] = quantity

    if request.method == "POST":
        import secrets
        from datetime import timedelta
        from django.core.exceptions import ValidationError
        from django.core.validators import validate_email
        from apps.checkout.models import Order, OrderLine, OrderStatus, ReceivableDue
        from apps.catalog.models import CapacityHold, HoldStatus
        from .raffle_sales import price_cents, reserve_online_capacity
        error = ""
        if not context["sales_open"]:
            error = "A venda de números pelo site não está aberta no momento."
        elif not context["payments_available"]:
            error = "O pagamento online ainda não está disponível. Tente mais tarde ou fale com um vendedor."
        elif not form_values["buyer_name"] or not form_values["buyer_phone"] or not form_values["buyer_email"]:
            error = "Preencha nome, WhatsApp e e-mail para receber seus números da sorte."
        elif post.get("rules_accepted") != "on":
            error = "Leia e aceite o regulamento do sorteio para continuar."
        else:
            try:
                validate_email(form_values["buyer_email"])
            except ValidationError:
                error = "Confira o e-mail informado."
        if error:
            messages.error(request, error)
            return render(request, "events/raffle_buy.html", context, status=400)
        try:
            with transaction.atomic():
                Event.objects.select_for_update().get(pk=event.pk)
                raffle = MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
                if not raffle.purchase_available:
                    raise ValidationError("A venda de números pelo site acabou de ser encerrada.")
                total_cents = price_cents(raffle, quantity)
                resource = reserve_online_capacity(raffle, quantity)
                hold_expires = min(timezone.now() + timedelta(minutes=35), raffle.draw_at)
                order = Order.objects.create(
                    event=event, merchant_reference=f"SO-{event.edition_number:02d}-{secrets.token_hex(3).upper()}",
                    purchaser_user=request.user if request.user.is_authenticated else None,
                    purchaser_name=form_values["buyer_name"], purchaser_email=form_values["buyer_email"],
                    purchaser_phone=form_values["buyer_phone"], purchaser_document=form_values["buyer_doc"],
                    status=OrderStatus.RESERVED, expires_at=hold_expires, currency="BRL",
                    gross_cents=total_cents, total_cents=total_cents)
                line = OrderLine.objects.create(
                    order=order, raffle=raffle, quantity=quantity, unit_cents=total_cents // quantity, net_cents=total_cents,
                    intended_person_name=form_values["buyer_name"], intended_person_email=form_values["buyer_email"],
                    intended_person_phone=form_values["buyer_phone"],
                    item_snapshot_json={"kind": "RAFFLE", "product_name": f"Números da sorte ({quantity}x)", "price_cents": total_cents // quantity,
                        "total_cents": total_cents, "quantity": quantity, "raffle_id": str(raffle.pk), "title": raffle.title,
                        "rules_sha256": hashlib.sha256(raffle.rules_text.encode()).hexdigest(),
                        "rules_accepted_at": timezone.now().isoformat(), "authorization_reference": raffle.authorization_reference})
                CapacityHold.objects.create(resource=resource, order_id=order.id, order_line_id=line.id, quantity=quantity,
                    status=HoldStatus.ACTIVE, expires_at=hold_expires, idempotency_key=f"HOLD-RAFFLE-{order.id}-{line.id}")
                ReceivableDue.objects.create(order=order, due_at=hold_expires, amount_cents=total_cents)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "events/raffle_buy.html", _buy_context(event, raffle, quantity=quantity, **form_values), status=400)
        authorized_ids = request.session.get("checkout_order_ids", [])
        request.session["checkout_order_ids"] = [str(v) for v in authorized_ids[-49:]] + [str(order.id)]
        return redirect("checkout:order_payment", order_id=order.id)

    return render(request, "events/raffle_buy.html", context)


def raffle_root_shortcut(request):
    event = Event.objects.filter(status__in=[EventStatus.PUBLISHED, EventStatus.IN_PROGRESS]).order_by("-starts_at").first()
    if event:
        return redirect("events:raffle_buy", slug=event.slug)
    return redirect("events:list")

