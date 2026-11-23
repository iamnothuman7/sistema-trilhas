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
    return {"event": event, "raffle": raffle, "raffle_preview": preview,
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


def calculate_raffle_price_cents(quantity):
    """
    Desconto progressivo de 5 em 5:
    1 Ponto = R$ 20,00
    Combo 5 Pontos = R$ 80,00
    Combo 10 Pontos = R$ 150,00
    Combo 15 Pontos = R$ 210,00
    Combo 20 Pontos = R$ 260,00
    """
    if quantity <= 0:
        return 0
    table = {1: 2000, 5: 8000, 10: 15000, 15: 21000, 20: 26000}
    if quantity in table:
        return table[quantity]
    q = quantity
    total = 0
    while q >= 20:
        total += 26000
        q -= 20
    while q >= 15:
        total += 21000
        q -= 15
    while q >= 10:
        total += 15000
        q -= 10
    while q >= 5:
        total += 8000
        q -= 5
    total += q * 2000
    return total


@require_http_methods(["GET", "POST"])
def raffle_standalone_buy(request, slug):
    """
    Fluxo de compra avulsa de números da sorte no site principal com descontos de 5 em 5.
    Não exige ser piloto nem ter inscrição ativa.
    """
    event = get_event_resilient(slug, request)
    raffle = get_object_or_404(MotorcycleRaffle, event=event, is_public=True)
    
    from apps.payments.gateway import MercadoPagoGateway
    payments_available = not MercadoPagoGateway().configuration_issues()
    
    combos = [
        {"qty": 1, "label": "1 Ponto", "price_reais": "20,00", "economy": None, "popular": False},
        {"qty": 5, "label": "5 Pontos", "price_reais": "80,00", "economy": "Economize R$ 20", "popular": True},
        {"qty": 10, "label": "10 Pontos", "price_reais": "150,00", "economy": "Economize R$ 50", "popular": False},
        {"qty": 15, "label": "15 Pontos", "price_reais": "210,00", "economy": "Economize R$ 90", "popular": False},
        {"qty": 20, "label": "20 Pontos", "price_reais": "260,00", "economy": "Economize R$ 140", "popular": False},
    ]

    buyer_name = request.POST.get("buyer_name", "").strip() if request.method == "POST" else ""
    buyer_phone = request.POST.get("buyer_phone", "").strip() if request.method == "POST" else ""
    buyer_email = request.POST.get("buyer_email", "").strip().lower() if request.method == "POST" else ""
    buyer_doc = request.POST.get("buyer_document", "").strip() if request.method == "POST" else ""

    try:
        quantity = int(request.POST.get("quantity", "5"))
    except (ValueError, TypeError):
        quantity = 5
    quantity = max(1, min(100, quantity))

    if request.method == "POST":
        if not buyer_name or not buyer_phone or not buyer_email:
            messages.error(request, "Por favor, preencha seu nome, WhatsApp e e-mail para receber seus números da sorte.")
            return render(request, "events/raffle_buy.html", {
                "event": event, "raffle": raffle, "combos": combos,
                "payments_available": payments_available, "quantity": quantity,
                "buyer_name": buyer_name, "buyer_phone": buyer_phone, "buyer_email": buyer_email, "buyer_doc": buyer_doc
            }, status=400)
            
        import secrets
        from datetime import timedelta
        from apps.checkout.models import Order, OrderLine, OrderStatus, ReceivableDue
        from apps.catalog.models import CapacityResource, CapacityHold, HoldStatus
        
        total_cents = calculate_raffle_price_cents(quantity)
        hold_expires = timezone.now() + timedelta(minutes=35)
        
        with transaction.atomic():
            resource, _ = CapacityResource.objects.select_for_update().get_or_create(
                event=event, type="RAFFLE_TOTAL", reference_id=str(raffle.pk),
                defaults={"capacity_total": raffle.quota_quantity or 10000}
            )
            resource.reserved_count += quantity
            resource.save(update_fields=["reserved_count"])
            
            ref_code = f"SO-{event.edition_number:02d}-{secrets.token_hex(3).upper()}"
            order = Order.objects.create(
                event=event,
                merchant_reference=ref_code,
                purchaser_user=request.user if request.user.is_authenticated else None,
                purchaser_name=buyer_name,
                purchaser_email=buyer_email,
                purchaser_phone=buyer_phone,
                purchaser_document=buyer_doc,
                status=OrderStatus.RESERVED,
                expires_at=hold_expires,
                currency="BRL",
                gross_cents=total_cents,
                total_cents=total_cents,
            )
            
            unit_price = total_cents // quantity
            line = OrderLine.objects.create(
                order=order,
                raffle=raffle,
                quantity=quantity,
                unit_cents=unit_price,
                net_cents=total_cents,
                intended_person_name=buyer_name,
                intended_person_email=buyer_email,
                intended_person_phone=buyer_phone,
                item_snapshot_json={
                    "kind": "RAFFLE",
                    "product_name": f"Pontos do Sorteio da Moto ({quantity}x)",
                    "price_cents": unit_price,
                    "total_cents": total_cents,
                    "quantity": quantity,
                    "raffle_id": str(raffle.pk),
                    "title": raffle.title,
                }
            )
            
            CapacityHold.objects.create(
                resource=resource,
                order_id=order.id,
                order_line_id=line.id,
                quantity=quantity,
                status=HoldStatus.ACTIVE,
                expires_at=hold_expires,
                idempotency_key=f"HOLD-RAFFLE-{order.id}-{line.id}"
            )
            
            ReceivableDue.objects.create(order=order, due_at=hold_expires, amount_cents=total_cents)
            
        authorized_ids = request.session.get("checkout_order_ids", [])
        request.session["checkout_order_ids"] = [str(v) for v in authorized_ids[-49:]] + [str(order.id)]
        return redirect("checkout:order_payment", order_id=order.id)

    return render(request, "events/raffle_buy.html", {
        "event": event, "raffle": raffle, "combos": combos,
        "payments_available": payments_available, "quantity": quantity,
        "buyer_name": buyer_name, "buyer_phone": buyer_phone, "buyer_email": buyer_email, "buyer_doc": buyer_doc
    })


def raffle_root_shortcut(request):
    event = Event.objects.filter(status__in=[EventStatus.PUBLISHED, EventStatus.IN_PROGRESS]).order_by("-starts_at").first()
    if event:
        return redirect("events:raffle_buy", slug=event.slug)
    return redirect("events:list")

