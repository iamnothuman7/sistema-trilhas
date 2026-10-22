import uuid
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.db import transaction
from django.utils import timezone
from apps.events.models import Event, EventStatus, EventTheme, EventType
from apps.catalog.models import Product, Lot, Coupon, CapacityResource, InventoryItem
from apps.checkout.models import Order, OrderStatus
from apps.registrations.models import (
    Registration, CommercialState, OperationalState,
    Entitlement, EntitlementState, Consumption, RegistrationCredential
)
from apps.operations.models import CheckinRecord, CheckinType
from apps.finance.models import FinancialEntry, Expense, Vendor
from apps.expeditions.models import ExpeditionDay, Room, RoomAssignment, TransportVehicle
from apps.mediaframes.models import FrameTemplate
from apps.core.models import AuditLog
from apps.core.utils import log_audit, cents_to_reais
from apps.reporting.selectors import get_event_kpi_summary
from apps.reporting.exports import export_participants_csv, export_financial_csv

def staff_required_custom(view_func):
    """Verifica se o usuário tem permissão de acesso à gestão."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser or request.user.event_memberships.filter(revoked_at__isnull=True).exists()):
            messages.error(request, "Acesso restrito à equipe gestora da Equipe Morcego.")
            return redirect("core:home")
        return view_func(request, *args, **kwargs)
    return wrapper

@staff_required_custom
def dashboard(request):
    events = Event.objects.all().order_by("-starts_at")
    selected_event_id = request.GET.get("event_id")
    event = None
    if selected_event_id:
        event = Event.objects.filter(id=selected_event_id).first()
    if not event:
        event = events.first()

    kpi = get_event_kpi_summary(event) if event else {}
    recent_orders = Order.objects.filter(event=event).order_by("-created_at")[:8] if event else []
    recent_checkins = CheckinRecord.objects.filter(event=event).select_related("registration", "registration__person").order_by("-occurred_at")[:6] if event else []

    return render(request, "gestao/dashboard.html", {
        "events": events,
        "selected_event": event,
        "kpi": kpi,
        "recent_orders": recent_orders,
        "recent_checkins": recent_checkins,
    })


@staff_required_custom
def event_list(request):
    events = Event.objects.all().order_by("-starts_at")
    return render(request, "gestao/events/event_list.html", {"events": events})


@staff_required_custom
def event_create(request):
    if request.method == "POST":
        title = request.POST.get("title")
        slug = request.POST.get("slug")
        edition = int(request.POST.get("edition_number", 1))
        etype = request.POST.get("type", EventType.TRILHAO)
        city = request.POST.get("city", "Tianguá")
        venue = request.POST.get("venue", "Polo de Lazer")
        starts_at = request.POST.get("starts_at")
        ends_at = request.POST.get("ends_at")
        reg_opens = request.POST.get("registration_opens_at")
        reg_closes = request.POST.get("registration_closes_at")
        max_attendees = int(request.POST.get("max_attendees", 300))
        description = request.POST.get("description", "")

        from apps.core.models import Organization
        org = Organization.objects.first()

        event = Event.objects.create(
            organization=org,
            title=title,
            slug=slug,
            edition_number=edition,
            type=etype,
            status=EventStatus.DRAFT,
            city=city,
            venue=venue,
            starts_at=starts_at,
            ends_at=ends_at,
            registration_opens_at=reg_opens,
            registration_closes_at=reg_closes,
            max_attendees=max_attendees,
            description=description,
        )

        EventTheme.objects.create(event=event)
        log_audit(actor=request.user, event_id=event.id, action="EVENT_CREATED", entity_type="Event", entity_id=event.id, request=request)
        messages.success(request, f"Evento '{event.title}' criado em rascunho com sucesso!")
        return redirect("gestao:event_detail", event_id=event.id)

    return render(request, "gestao/events/event_form.html")


@staff_required_custom
def event_detail(request, event_id):
    event = get_object_or_404(Event.objects.select_related("theme"), id=event_id)
    kpi = get_event_kpi_summary(event)
    return render(request, "gestao/events/event_detail.html", {
        "event": event,
        "kpi": kpi,
    })


@staff_required_custom
def event_edit(request, event_id):
    event = get_object_or_404(Event.objects.select_related("theme"), id=event_id)
    if request.method == "POST":
        event.title = request.POST.get("title", event.title)
        event.status = request.POST.get("status", event.status)
        event.city = request.POST.get("city", event.city)
        event.venue = request.POST.get("venue", event.venue)
        event.max_attendees = int(request.POST.get("max_attendees", event.max_attendees))
        event.description = request.POST.get("description", event.description)
        event.save()

        # Tema
        theme = event.theme
        theme.primary_color = request.POST.get("primary_color", theme.primary_color)
        theme.secondary_color = request.POST.get("secondary_color", theme.secondary_color)
        theme.surface_color = request.POST.get("surface_color", theme.surface_color)
        theme.save()

        log_audit(actor=request.user, event_id=event.id, action="EVENT_UPDATED", entity_type="Event", entity_id=event.id, request=request)
        messages.success(request, "Evento atualizado com sucesso!")
        return redirect("gestao:event_detail", event_id=event.id)

    return render(request, "gestao/events/event_edit.html", {"event": event})


@staff_required_custom
def event_duplicate(request, event_id):
    """
    Duplica um evento com estrutura, produtos, temas e páginas em rascunho.
    NUNCA duplica inscrições, pagamentos ou dados pessoais (Invariante do Sistema).
    """
    source = get_object_or_404(Event, id=event_id)

    with transaction.atomic():
        new_event = Event.objects.create(
            organization=source.organization,
            title=f"{source.title} (Nova Edição)",
            slug=f"{source.slug}-copia-{uuid.uuid4().hex[:4]}",
            edition_number=source.edition_number + 1,
            type=source.type,
            status=EventStatus.DRAFT,
            city=source.city,
            state=source.state,
            venue=source.venue,
            starts_at=source.starts_at + timezone.timedelta(days=365),
            ends_at=source.ends_at + timezone.timedelta(days=365),
            registration_opens_at=timezone.now(),
            registration_closes_at=source.ends_at + timezone.timedelta(days=360),
            max_attendees=source.max_attendees,
            description=source.description,
            rules_summary=source.rules_summary,
        )

        # Copiar Tema
        if hasattr(source, "theme"):
            EventTheme.objects.create(
                event=new_event,
                primary_color=source.theme.primary_color,
                secondary_color=source.theme.secondary_color,
                accent_color=source.theme.accent_color,
                surface_color=source.theme.surface_color,
            )

        # Copiar Produtos e Lotes
        for prod in source.products.all():
            new_prod = Product.objects.create(
                event=new_event,
                kind=prod.kind,
                name=prod.name,
                description=prod.description,
                active=True,
                includes_shirt=prod.includes_shirt,
                includes_meal=prod.includes_meal,
                includes_kit=prod.includes_kit,
            )
            for lot in prod.lots.all():
                Lot.objects.create(
                    event=new_event,
                    product=new_prod,
                    title=lot.title,
                    price_cents=lot.price_cents,
                    opens_at=timezone.now(),
                    closes_at=new_event.registration_closes_at,
                    max_quantity=lot.max_quantity,
                    priority=lot.priority,
                    active=True,
                )

        log_audit(actor=request.user, event_id=new_event.id, action="EVENT_DUPLICATED", entity_type="Event", entity_id=new_event.id, diff={"source_id": str(source.id)}, request=request)
        messages.success(request, f"Edição duplicada com sucesso em rascunho como '{new_event.title}'!")
        return redirect("gestao:event_detail", event_id=new_event.id)


@staff_required_custom
def registration_list(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    regs = Registration.objects.filter(event=event).select_related("person", "source_order_line", "credential").order_by("registration_number", "-created_at")

    # Filtros
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status")
    op_filter = request.GET.get("op_status")

    if q:
        regs = regs.filter(
            models_q := (
                Registration.objects.filter(person__full_name__icontains=q) |
                Registration.objects.filter(person__phone__icontains=q) |
                Registration.objects.filter(team_name__icontains=q) |
                Registration.objects.filter(person__city__icontains=q) |
                Registration.objects.filter(registration_number__icontains=q)
            )
        )
    if status_filter:
        regs = regs.filter(commercial_state=status_filter)
    if op_filter:
        regs = regs.filter(operational_state=op_filter)

    return render(request, "gestao/registrations/registration_list.html", {
        "event": event,
        "registrations": regs[:150],
        "q": q,
        "status_filter": status_filter,
        "op_filter": op_filter,
    })


@staff_required_custom
def registration_detail(request, event_id, reg_id):
    event = get_object_or_404(Event, id=event_id)
    reg = get_object_or_404(
        Registration.objects.select_related("person", "source_order_line", "source_order_line__order", "credential"),
        id=reg_id,
        event=event
    )
    entitlements = reg.entitlements.all()
    terms = reg.terms_acceptances.all()
    consumptions = Consumption.objects.filter(entitlement__registration=reg).select_related("operator", "entitlement")

    return render(request, "gestao/registrations/registration_detail.html", {
        "event": event,
        "reg": reg,
        "entitlements": entitlements,
        "terms": terms,
        "consumptions": consumptions,
    })


@staff_required_custom
def registration_change_status(request, event_id, reg_id):
    event = get_object_or_404(Event, id=event_id)
    reg = get_object_or_404(Registration, id=reg_id, event=event)

    if request.method == "POST":
        new_status = request.POST.get("commercial_state")
        reason = request.POST.get("reason", "")
        old_status = reg.commercial_state

        if new_status in CommercialState.values:
            reg.commercial_state = new_status
            reg.save()
            log_audit(actor=request.user, event_id=event.id, action="REGISTRATION_STATUS_CHANGED", entity_type="Registration", entity_id=reg.id, diff={"old": old_status, "new": new_status}, reason=reason, request=request)
            messages.success(request, f"Situação da inscrição atualizada para {reg.get_commercial_state_display()}.")

    return redirect("gestao:registration_detail", event_id=event.id, reg_id=reg.id)


@staff_required_custom
def credenciamento_scanner(request, event_id):
    """
    Interface móvel de alta velocidade para operadores de credenciamento.
    Leitura de QR Code pela câmera ou digitação rápida do código.
    """
    event = get_object_or_404(Event.objects.select_related("theme"), id=event_id)
    recent_checkins = CheckinRecord.objects.filter(event=event).select_related("registration", "registration__person").order_by("-occurred_at")[:10]

    return render(request, "gestao/credenciamento/scanner.html", {
        "event": event,
        "recent_checkins": recent_checkins,
    })


@staff_required_custom
def credenciamento_lookup_api(request, event_id):
    """
    API JSON que resolve código do QR ou busca rápida.
    Retorna apenas dados necessários para a conferência operacional (LGPD).
    """
    code = request.GET.get("code", "").strip()
    if not code:
        return JsonResponse({"found": False, "error": "Código não informado."})

    # Tratamento se o QR vier no formato TRILHA:slug:TOKEN
    if ":" in code:
        parts = code.split(":")
        token = parts[-1]
    else:
        token = code

    # Localizar credencial pelo raw_token ou token_digest
    cred = RegistrationCredential.objects.filter(
        registration__event_id=event_id,
        raw_token__iexact=token
    ).select_related("registration", "registration__person").first()

    if not cred:
        # Tenta buscar por número de inscrição ou nome
        reg = Registration.objects.filter(
            event_id=event_id
        ).filter(
            models_q := (
                Registration.objects.filter(registration_number__iexact=token) |
                Registration.objects.filter(person__full_name__icontains=token)
            )
        ).select_related("person", "credential").first()
    else:
        reg = cred.registration

    if not reg:
        return JsonResponse({"found": False, "error": "Participante não localizado neste evento."})

    has_checked_in = hasattr(reg, "checkin_record")
    entitlements_data = []
    for ent in reg.entitlements.all():
        entitlements_data.append({
            "id": str(ent.id),
            "benefit_type": ent.benefit_type,
            "title": ent.title,
            "quantity_total": ent.quantity_total,
            "quantity_used": ent.quantity_used,
            "remaining": ent.remaining,
            "is_consumed": ent.quantity_used >= ent.quantity_total,
        })

    return JsonResponse({
        "found": True,
        "registration_id": str(reg.id),
        "number": reg.registration_number,
        "name": reg.person.full_name,
        "team": reg.team_name or reg.person.team_name or "-",
        "category": reg.category_name,
        "shirt_size": reg.shirt_size,
        "commercial_state": reg.commercial_state,
        "is_active": reg.commercial_state == CommercialState.ACTIVE,
        "operational_state": reg.operational_state,
        "has_checked_in": has_checked_in,
        "checked_in_at": reg.checkin_record.occurred_at.strftime("%H:%M") if has_checked_in else None,
        "entitlements": entitlements_data,
    })


@staff_required_custom
def credenciamento_confirm_api(request, event_id):
    """
    Confirma presença (Check-in) com trava atômica.
    Idempotente: cliques repetidos não geram duplicatas.
    """
    if request.method != "POST":
        return HttpResponseBadRequest()

    reg_id = request.POST.get("registration_id")
    reg = get_object_or_404(Registration, id=reg_id, event_id=event_id)

    source_key = f"CHECKIN-{event_id}-{reg.id}"
    checkin, created = CheckinRecord.objects.get_or_create(
        registration=reg,
        event_id=event_id,
        defaults={
            "operator": request.user,
            "source_key": source_key,
            "checkin_type": CheckinType.SCAN,
            "occurred_at": timezone.now(),
        }
    )

    reg.operational_state = OperationalState.CHECKED_IN
    reg.save()

    return JsonResponse({
        "success": True,
        "already_checked_in": not created,
        "occurred_at": checkin.occurred_at.strftime("%d/%m %H:%M"),
    })


@staff_required_custom
def deliver_benefit_api(request, event_id):
    """
    Entrega benefício (Kit, Camiseta, Refeição) atômica com lock contra entrega duplicada.
    """
    if request.method != "POST":
        return HttpResponseBadRequest()

    ent_id = request.POST.get("entitlement_id")
    with transaction.atomic():
        ent = Entitlement.objects.select_for_update().filter(id=ent_id, registration__event_id=event_id).first()
        if not ent:
            return JsonResponse({"success": False, "error": "Benefício não encontrado."})

        if ent.remaining <= 0:
            return JsonResponse({
                "success": False,
                "error": "Este item já foi totalmente entregue!",
                "already_consumed": True
            })

        source_key = f"DELIVER-{ent.id}-{ent.quantity_used + 1}"
        Consumption.objects.create(
            entitlement=ent,
            quantity=1,
            action="DELIVERED",
            operator=request.user,
            source_key=source_key,
            occurred_at=timezone.now(),
        )

        ent.quantity_used += 1
        if ent.quantity_used >= ent.quantity_total:
            ent.state = EntitlementState.CONSUMED
        else:
            ent.state = EntitlementState.PARTIALLY_USED
        ent.save()

    return JsonResponse({
        "success": True,
        "remaining": ent.remaining,
        "quantity_used": ent.quantity_used,
        "is_consumed": ent.quantity_used >= ent.quantity_total,
    })


@staff_required_custom
def catalog_management(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    products = Product.objects.filter(event=event).prefetch_related("lots", "variants")
    coupons = Coupon.objects.filter(event=event).order_by("-valid_until")
    inventory = InventoryItem.objects.filter(event=event)

    return render(request, "gestao/catalog/catalog.html", {
        "event": event,
        "products": products,
        "coupons": coupons,
        "inventory": inventory,
    })


@staff_required_custom
def finance_overview(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    kpi = get_event_kpi_summary(event)
    entries = FinancialEntry.objects.filter(event=event).order_by("-recognized_at")[:50]
    expenses = Expense.objects.filter(event=event).order_by("-due_at")
    vendors = Vendor.objects.filter(organization=event.organization)

    return render(request, "gestao/finance/overview.html", {
        "event": event,
        "kpi": kpi,
        "entries": entries,
        "expenses": expenses,
        "vendors": vendors,
    })


@staff_required_custom
def add_expense(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    if request.method == "POST":
        desc = request.POST.get("description")
        vendor_id = request.POST.get("vendor_id")
        cat = request.POST.get("category")
        amount_reais = float(request.POST.get("amount", 0).replace(",", "."))
        amount_cents = int(amount_reais * 100)
        due_at = request.POST.get("due_at")

        vendor = Vendor.objects.filter(id=vendor_id).first() if vendor_id else None

        Expense.objects.create(
            event=event,
            vendor=vendor,
            category=cat,
            description=desc,
            amount_cents=amount_cents,
            due_at=due_at,
            paid_at=timezone.now().date(),
            status="PAID"
        )

        FinancialEntry.objects.create(
            event=event,
            source_reference=f"EXPENSE-{uuid.uuid4().hex[:8]}",
            kind="DESPESA",
            amount_cents_signed=-amount_cents,
            description=f"Despesa: {desc}",
            posted_by=request.user,
            recognized_at=timezone.now(),
        )

        messages.success(request, "Despesa registrada com sucesso!")
        return redirect("gestao:finance", event_id=event.id)

    return redirect("gestao:finance", event_id=event.id)


@staff_required_custom
def expedition_management(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    days = ExpeditionDay.objects.filter(event=event).order_by("day_number")
    rooms = Room.objects.filter(event=event).prefetch_related("assignments", "assignments__registration__person")
    vehicles = TransportVehicle.objects.filter(event=event).prefetch_related("assignments")

    return render(request, "gestao/expeditions/expeditions.html", {
        "event": event,
        "days": days,
        "rooms": rooms,
        "vehicles": vehicles,
    })


@staff_required_custom
def frame_management(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    templates = FrameTemplate.objects.filter(event=event)

    if request.method == "POST":
        title = request.POST.get("title")
        fmt = request.POST.get("format")
        overlay = request.FILES.get("overlay_asset")
        access = request.POST.get("access_policy")

        width = 1080
        height = 1350 if fmt == "FEED_1080x1350" else 1920

        if overlay:
            FrameTemplate.objects.create(
                event=event,
                title=title,
                format=fmt,
                width=width,
                height=height,
                overlay_asset=overlay,
                access_policy=access,
            )
            messages.success(request, "Modelo de moldura adicionado com sucesso!")
            return redirect("gestao:frames", event_id=event.id)

    return render(request, "gestao/mediaframes/frame_list.html", {
        "event": event,
        "templates": templates,
    })


@staff_required_custom
def export_participants(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    log_audit(actor=request.user, event_id=event.id, action="EXPORT_PARTICIPANTS_CSV", entity_type="Event", entity_id=event.id, request=request)
    return export_participants_csv(event)


@staff_required_custom
def export_financial(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    log_audit(actor=request.user, event_id=event.id, action="EXPORT_FINANCIAL_CSV", entity_type="Event", entity_id=event.id, request=request)
    return export_financial_csv(event)


@staff_required_custom
def audit_log_view(request):
    logs = AuditLog.objects.all().order_by("-created_at")[:100]
    return render(request, "gestao/audit/audit_log.html", {"logs": logs})
