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
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from apps.finance.forms import ExpenseForm
from apps.finance.models import ExpenseStatus
from apps.finance.services import create_expense, settle_expense, cancel_pending_expense
from apps.reporting.forms import DateRangeForm
from apps.reporting.selectors import financial_entries, get_financial_summary, in_period
from urllib.parse import urlencode
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from apps.core.permissions import management_required, accessible_events, can_manage_event, can_create_events, is_administrator

def staff_required_custom(view_func):
    """Authorise each action and event before resolving management data."""
    return management_required(view_func)

@staff_required_custom
def dashboard(request):
    events = accessible_events(request.user).order_by("-starts_at")
    selected_event_id = request.GET.get("event_id")
    event = None
    if selected_event_id:
        try:
            event = events.filter(id=uuid.UUID(selected_event_id)).first()
        except (ValueError, TypeError):
            event = None
    if not event:
        event = events.first()

    if event:
        request.session["management_event_id"] = str(event.id)
        request.management_event = event
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
    events = accessible_events(request.user, "events").order_by("-starts_at")
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
@require_POST
def event_duplicate(request, event_id):
    """
    Duplica um evento com estrutura, produtos, temas e páginas em rascunho.
    NUNCA duplica inscrições, pagamentos ou dados pessoais (Invariante do Sistema).
    """
    if not can_create_events(request.user):
        return HttpResponseForbidden("A duplicação de eventos exige acesso de administração geral.")
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
            Q(person__full_name__icontains=q) | Q(person__phone__icontains=q)
            | Q(team_name__icontains=q) | Q(person__city__icontains=q)
            | Q(registration_number__icontains=q)
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
@transaction.atomic
def registration_change_status(request, event_id, reg_id):
    event = get_object_or_404(Event, id=event_id)
    reg = get_object_or_404(Registration.objects.select_for_update(), id=reg_id, event=event)

    if request.method == "POST":
        new_status = request.POST.get("commercial_state")
        reason = request.POST.get("reason", "")
        old_status = reg.commercial_state

        if new_status in CommercialState.values:
            reg.commercial_state = new_status
            reg.save(update_fields=["commercial_state", "updated_at"])
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
            Q(registration_number__iexact=token) | Q(person__full_name__icontains=token)
        ).select_related("person", "credential").first()
    else:
        reg = cred.registration

    if not reg:
        return JsonResponse({"found": False, "error": "Participante não localizado neste evento."})

    if hasattr(reg, "credential") and not reg.credential.is_valid:
        return JsonResponse({"found": False, "error": "Credencial revogada ou expirada."})

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
            "state": ent.state,
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
@transaction.atomic
def credenciamento_confirm_api(request, event_id):
    """
    Confirma presença (Check-in) com trava atômica.
    Idempotente: cliques repetidos não geram duplicatas.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        reg_id = uuid.UUID(request.POST.get("registration_id", ""))
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Inscrição inválida."}, status=400)
    reg = get_object_or_404(Registration.objects.select_for_update(), id=reg_id, event_id=event_id)
    if reg.commercial_state != CommercialState.ACTIVE or reg.operational_state not in (OperationalState.READY, OperationalState.CHECKED_IN):
        return JsonResponse({"success": False, "error": "Inscrição sem pagamento confirmado ou aceite dos termos."}, status=409)
    if hasattr(reg, "credential") and not reg.credential.is_valid:
        return JsonResponse({"success": False, "error": "Credencial revogada ou expirada."}, status=409)

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
        return HttpResponseNotAllowed(["POST"])

    try:
        ent_id = uuid.UUID(request.POST.get("entitlement_id", ""))
        idempotency_key = uuid.UUID(request.POST.get("idempotency_key", ""))
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Benefício ou identificação da entrega inválidos."}, status=400)
    registration_id = Entitlement.objects.filter(id=ent_id, registration__event_id=event_id).values_list("registration_id", flat=True).first()
    if not registration_id:
        return JsonResponse({"success": False, "error": "Benefício não encontrado."}, status=404)
    with transaction.atomic():
        # Payment/refund operations also lock registration before its benefits.
        reg = get_object_or_404(Registration.objects.select_for_update(), id=registration_id, event_id=event_id)
        ent = Entitlement.objects.select_for_update().filter(id=ent_id, registration_id=reg.pk).first()
        if not ent:
            return JsonResponse({"success": False, "error": "Benefício não encontrado."}, status=404)

        source_key = f"DELIVER-{ent.id}-{idempotency_key}"
        if Consumption.objects.filter(entitlement=ent, source_key=source_key, action="DELIVERED").exists():
            return JsonResponse({
                "success": True, "already_delivered": True,
                "remaining": ent.remaining, "quantity_used": ent.quantity_used,
                "is_consumed": ent.quantity_used >= ent.quantity_total,
            })

        if ent.state == EntitlementState.REVOKED or reg.commercial_state != CommercialState.ACTIVE or reg.operational_state not in (OperationalState.READY, OperationalState.CHECKED_IN):
            return JsonResponse({"success": False, "error": "Benefício indisponível ou inscrição não apta para retirada."}, status=409)
        if hasattr(reg, "credential") and not reg.credential.is_valid:
            return JsonResponse({"success": False, "error": "Credencial revogada ou expirada."}, status=409)

        if ent.remaining <= 0:
            return JsonResponse({
                "success": False,
                "error": "Este item já foi totalmente entregue!",
                "already_consumed": True
            })

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
        "already_delivered": False,
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
    from apps.core.permissions import require_event_access
    event = require_event_access(request, event_id, action="finance")
    context = _finance_context(request, event)
    response = render(request, "gestao/finance/overview.html", context, status=400 if context["date_form"].errors else 200)
    response["Cache-Control"] = "private, no-store"
    return response


def _finance_context(request, event, expense_form=None):
    date_form = DateRangeForm(request.GET)
    valid = date_form.is_valid()
    start = date_form.cleaned_data.get("date_start") if valid else None
    end = date_form.cleaned_data.get("date_end") if valid else None
    entries = financial_entries(event, start, end).order_by("-recognized_at", "-pk") if valid else FinancialEntry.objects.none()
    expenses = in_period(Expense.objects.filter(event=event).select_related("vendor"), "due_at", start, end, date_field=True).order_by("-due_at", "-pk") if valid else Expense.objects.none()
    form = expense_form if expense_form is not None else ExpenseForm(event=event)
    entries_page = Paginator(entries, 30).get_page(request.GET.get("entries_page"))
    expenses_page = Paginator(expenses, 20).get_page(request.GET.get("expenses_page"))
    return {
        "event": event, "kpi": get_event_kpi_summary(event),
        "finance": get_financial_summary(event, start, end) if valid else {},
        "entries": entries_page.object_list, "expenses": expenses_page.object_list,
        "entries_page": entries_page,
        "expenses_page": expenses_page,
        "vendors": Vendor.objects.filter(organization=event.organization).order_by("name"),
        "date_form": date_form, "expense_form": form, "expense_form_errors": form.errors,
        "today": timezone.localdate().isoformat(),
        "filter_query": urlencode({key: value.isoformat() for key, value in date_form.cleaned_data.items() if value}) if valid else "",
    }


@staff_required_custom
@require_POST
def add_expense(request, event_id):
    from apps.core.permissions import require_event_access
    event = require_event_access(request, event_id, action="finance")
    form = ExpenseForm(request.POST, event=event)
    if form.is_valid():
        try:
            expense, created = create_expense(event=event, data=form.cleaned_data, actor=request.user, request=request)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Despesa registrada com sucesso." if created else "Esta despesa já estava registrada; nenhum lançamento foi duplicado.")
            return redirect("gestao:finance", event_id=event.pk)
    return render(request, "gestao/finance/overview.html", _finance_context(request, event, form), status=400)


@staff_required_custom
@require_POST
def pay_expense(request, event_id, expense_id):
    from django import forms
    from apps.core.permissions import require_event_access
    event = require_event_access(request, event_id, action="finance")
    get_object_or_404(Expense, pk=expense_id, event=event)
    try:
        paid_at = forms.DateField(input_formats=["%Y-%m-%d"]).clean(request.POST.get("paid_at") or timezone.localdate().isoformat())
        _, changed = settle_expense(event=event, expense_id=expense_id, actor=request.user, paid_at=paid_at, request=request)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Pagamento registrado no extrato." if changed else "Esta despesa já está paga.")
    return redirect("gestao:finance", event_id=event.pk)


@staff_required_custom
@require_POST
def cancel_expense(request, event_id, expense_id):
    from apps.core.permissions import require_event_access
    event = require_event_access(request, event_id, action="finance")
    get_object_or_404(Expense, pk=expense_id, event=event)
    try:
        _, changed = cancel_pending_expense(event=event, expense_id=expense_id, actor=request.user, reason=request.POST.get("reason"), request=request)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Despesa cancelada. O histórico foi preservado." if changed else "Esta despesa já está cancelada.")
    return redirect("gestao:finance", event_id=event.pk)


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
    from apps.core.permissions import require_event_access
    from django.http import HttpResponseBadRequest
    event = require_event_access(request, event_id, action="finance")
    form = DateRangeForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Período inválido. Confira as datas do relatório.")
    log_audit(actor=request.user, event_id=event.id, action="EXPORT_FINANCIAL_CSV", entity_type="Event", entity_id=event.id, request=request)
    return export_financial_csv(event, form.cleaned_data["date_start"], form.cleaned_data["date_end"])


@staff_required_custom
def audit_log_view(request):
    logs = AuditLog.objects.all()
    if not is_administrator(request.user):
        logs = logs.filter(event_id__in=accessible_events(request.user, "audit").values("id"))
    logs = logs.order_by("-created_at")[:100]
    return render(request, "gestao/audit/audit_log.html", {"logs": logs})
