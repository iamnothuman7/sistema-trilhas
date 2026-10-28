import uuid
from pathlib import Path
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.db import transaction, IntegrityError
from django.utils import timezone
from apps.events.models import Event, EventStatus, EventTheme, EventType, EventPageBlock
from apps.catalog.models import Product, ProductVariant, Lot, Coupon, CapacityResource, InventoryItem
from apps.checkout.models import Order, OrderStatus
from apps.registrations.models import (
    Registration, CommercialState, OperationalState,
    Entitlement, EntitlementState, Consumption, RegistrationCredential
)
from apps.operations.models import CheckinRecord, CheckinType
from apps.finance.models import FinancialEntry, Expense, Vendor
from apps.mediaframes.models import FrameTemplate
from apps.core.models import AuditLog
from apps.core.utils import log_audit, cents_to_reais
from apps.reporting.selectors import get_event_kpi_summary
from apps.reporting.exports import export_participants_csv, export_financial_csv
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST, require_GET
from apps.finance.forms import ExpenseForm
from apps.finance.models import ExpenseStatus
from apps.finance.services import create_expense, settle_expense, cancel_pending_expense
from apps.reporting.forms import DateRangeForm
from apps.reporting.selectors import financial_entries, get_financial_summary, in_period
from urllib.parse import urlencode
from django.db.models import Q
from django.db.models import Count
from django.http import HttpResponseNotAllowed
from django.views.decorators.cache import never_cache
from apps.events.forms_management import EventManagementForm, EventAppearanceForm
from apps.core.permissions import management_required, accessible_events, can_manage_event, can_create_events, is_administrator, selected_management_event

def staff_required_custom(view_func):
    """Authorise each action and event before resolving management data."""
    return management_required(view_func)

@staff_required_custom
@never_cache
def usage_guide(request):
    return render(request, "gestao/usage_guide.html", {
        "guide_events": accessible_events(request.user).order_by("-starts_at"),
    })

@staff_required_custom
@never_cache
def dashboard(request):
    events = accessible_events(request.user).order_by("-starts_at")
    event = selected_management_event(request)
    kpi = get_event_kpi_summary(event) if event else {}
    show_recent_orders = bool(event and (can_manage_event(request.user, event, "finance") or
                                        can_manage_event(request.user, event, "registrations")))
    show_recent_checkins = bool(event and can_manage_event(request.user, event, "checkin"))
    # The overview obeys the same capabilities as each detailed workspace.
    recent_orders = Order.objects.filter(event=event).order_by("-created_at")[:8] if show_recent_orders else []
    recent_checkins = CheckinRecord.objects.filter(event=event).select_related("registration", "registration__person").order_by("-occurred_at")[:6] if show_recent_checkins else []

    return render(request, "gestao/dashboard.html", {
        "events": events,
        "selected_event": event,
        "kpi": kpi,
        "recent_orders": recent_orders,
        "recent_checkins": recent_checkins,
        "show_recent_orders": show_recent_orders,
        "show_recent_checkins": show_recent_checkins,
    })


@staff_required_custom
def event_list(request):
    events = accessible_events(request.user, "events").annotate(
        confirmed_count=Count("registrations", filter=Q(
            registrations__commercial_state=CommercialState.ACTIVE,
            registrations__archived_at__isnull=True,
        )),
    ).order_by("-starts_at")
    return render(request, "gestao/events/event_list.html", {"events": events})


@staff_required_custom
@never_cache
def event_create(request):
    form = EventManagementForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                event = form.save(commit=False)
                event.status = EventStatus.DRAFT
                # Explicitly choose a unique subdomain; changing a title later
                # never changes the address of an existing edition.
                base = event.slug.replace("-", "")[:80] or "evento"
                event.subdomain = base
                while Event.objects.filter(subdomain=event.subdomain).exists():
                    event.subdomain = f"{base}{uuid.uuid4().hex[:8]}"
                event.save()
                EventTheme.objects.create(event=event, primary_color="#f97316", secondary_color="#c8bdb4",
                                          accent_color="#ef5237", surface_color="#191512", text_color="#faf5f0")
                CapacityResource.objects.create(event=event, type="EVENT_OVERALL", capacity_total=event.max_attendees)
                log_audit(actor=request.user, event_id=event.id, action="EVENT_CREATED", entity_type="Event", entity_id=event.id, request=request)
        except IntegrityError:
            form.add_error(None, "Este endereço já foi utilizado. Escolha outro endereço e tente novamente.")
        else:
            messages.success(request, "Edição criada em rascunho. Revise a aparência, os lotes e as datas antes de publicar.")
            return redirect("gestao:event_detail", event_id=event.id)
    return render(request, "gestao/events/event_form.html", {"form": form, "creating": True})


@staff_required_custom
def event_detail(request, event_id):
    event = get_object_or_404(Event.objects.select_related("theme"), id=event_id)
    kpi = get_event_kpi_summary(event)
    return render(request, "gestao/events/event_detail.html", {
        "event": event,
        "kpi": kpi,
    })


@staff_required_custom
@never_cache
def event_edit(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    if request.method == "POST":
        with transaction.atomic():
            # Same lock order as checkout reservation: event, then capacity.
            event = Event.objects.select_for_update().get(pk=event_id)
            resources = list(CapacityResource.objects.select_for_update().filter(event=event, type="EVENT_OVERALL").order_by("pk"))
            form = EventManagementForm(request.POST, instance=event)
            if form.is_valid():
                changed = list(form.changed_data)
                event = form.save()
                if len(resources) == 1:
                    for resource in resources:
                        resource.capacity_total = event.max_attendees
                        resource.save(update_fields=["capacity_total"])
                elif not resources:
                    active = Registration.objects.filter(event=event, commercial_state=CommercialState.ACTIVE).count()
                    CapacityResource.objects.create(event=event, type="EVENT_OVERALL", capacity_total=event.max_attendees, committed_count=active)
                log_audit(actor=request.user, event_id=event.id, action="EVENT_UPDATED", entity_type="Event", entity_id=event.id,
                          diff={"fields": changed}, request=request)
                messages.success(request, "Dados da edição atualizados. Inscrições, pagamentos e histórico foram preservados.")
                return redirect("gestao:event_detail", event_id=event.id)
    else:
        form = EventManagementForm(instance=event)
    return render(request, "gestao/events/event_edit.html", {"event": event, "form": form, "creating": False},
                  status=409 if form.version_conflict else 200)


@staff_required_custom
@never_cache
def event_appearance(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    theme = EventTheme.objects.filter(event=event).first() or EventTheme(event=event)
    form = EventAppearanceForm(request.POST if request.method == "POST" else None, request.FILES or None, instance=theme)
    if request.method == "POST" and form.is_valid():
        written = []
        try:
            with transaction.atomic():
                Event.objects.select_for_update().get(pk=event_id)
                theme = EventTheme.objects.select_for_update().filter(event=event).first() or EventTheme(event=event)
                for name in form.fields:
                    value = form.cleaned_data[name]
                    if name in request.FILES:
                        suffix = Path(value.name).suffix.lower()
                        field = getattr(theme, name)
                        field.save(f"{uuid.uuid4().hex}{suffix}", value, save=False)
                        written.append((field.storage, field.name))
                    elif value is False:
                        setattr(theme, name, "")
                theme.save()
                log_audit(actor=request.user, event_id=event.id, action="EVENT_APPEARANCE_UPDATED", entity_type="EventTheme", entity_id=theme.pk,
                          diff={"fields": list(form.changed_data)}, request=request)
        except Exception:
            for storage, name in written:
                storage.delete(name)
            raise
        messages.success(request, "Aparência salva. Abra a página da edição para conferir o banner e a logo.")
        return redirect("gestao:event_appearance", event_id=event.id)
    return render(request, "gestao/events/event_appearance.html", {"event": event, "theme": theme, "form": form})


@staff_required_custom
@require_POST
def event_duplicate(request, event_id):
    """
    Duplica um evento com estrutura, produtos, temas e páginas em rascunho.
    NUNCA duplica inscrições, pagamentos ou dados pessoais (Invariante do Sistema).
    """
    if not can_create_events(request.user):
        return HttpResponseForbidden("A duplicação de eventos exige acesso de administração geral.")
    with transaction.atomic():
        source = get_object_or_404(Event.objects.select_for_update(), id=event_id)
        date_shift = timezone.timedelta(days=365)
        suffix = uuid.uuid4().hex[:12]
        new_event = Event.objects.create(
            organization=source.organization,
            title=f"{source.title[:186]} (Nova Edição)",
            slug=f"{source.slug[:181]}-copia-{suffix}",
            subdomain=f"{(source.subdomain or source.slug).replace('-', '')[:70]}copia{suffix}",
            edition_number=source.edition_number + 1,
            type=source.type,
            status=EventStatus.DRAFT,
            city=source.city,
            state=source.state,
            venue=source.venue,
            timezone=source.timezone,
            starts_at=source.starts_at + date_shift,
            ends_at=source.ends_at + date_shift,
            registration_opens_at=source.registration_opens_at + date_shift,
            registration_closes_at=source.registration_closes_at + date_shift,
            max_attendees=source.max_attendees,
            description=source.description,
            rules_summary=source.rules_summary,
            contact_email=source.contact_email,
            contact_phone=source.contact_phone,
        )

        # Copiar Tema
        if hasattr(source, "theme"):
            EventTheme.objects.create(
                event=new_event,
                primary_color=source.theme.primary_color,
                secondary_color=source.theme.secondary_color,
                accent_color=source.theme.accent_color,
                surface_color=source.theme.surface_color,
                text_color=source.theme.text_color,
                # Reuse immutable stored assets; no file is moved or deleted.
                hero_banner=source.theme.hero_banner.name,
                logo_image=source.theme.logo_image.name,
                custom_css=source.theme.custom_css,
            )

        # Copiar Produtos e Lotes
        for prod in source.products.all():
            new_prod = Product.objects.create(
                event=new_event,
                kind=prod.kind,
                name=prod.name,
                description=prod.description,
                active=prod.active,
                includes_shirt=prod.includes_shirt,
                includes_meal=prod.includes_meal,
                includes_kit=prod.includes_kit,
                default_benefits_json=prod.default_benefits_json,
            )
            ProductVariant.objects.bulk_create([
                ProductVariant(product=new_prod, kind=variant.kind, code=variant.code,
                               label=variant.label, extra_price_cents=variant.extra_price_cents)
                for variant in prod.variants.all()
            ])
            for lot in prod.lots.all():
                Lot.objects.create(
                    event=new_event,
                    product=new_prod,
                    title=lot.title,
                    price_cents=lot.price_cents,
                    opens_at=lot.opens_at + date_shift,
                    closes_at=lot.closes_at + date_shift,
                    max_quantity=lot.max_quantity,
                    current_sold=0,
                    priority=lot.priority,
                    active=lot.active,
                )

        EventPageBlock.objects.bulk_create([
            EventPageBlock(event=new_event, page_slug=block.page_slug, section_key=block.section_key,
                           title=block.title, sort_order=block.sort_order,
                           content_json=block.content_json, visible=block.visible)
            for block in source.page_blocks.all()
        ])
        # A new edition begins without sales or checkout reservations.
        CapacityResource.objects.create(event=new_event, type="EVENT_OVERALL", capacity_total=new_event.max_attendees)

        log_audit(actor=request.user, event_id=new_event.id, action="EVENT_DUPLICATED", entity_type="Event", entity_id=new_event.id, diff={"source_id": str(source.id)}, request=request)
        messages.success(request, f"Edição duplicada com sucesso em rascunho como '{new_event.title}'!")
        return redirect("gestao:event_detail", event_id=new_event.id)


@staff_required_custom
@never_cache
def registration_list(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    show_removed = request.GET.get('removed') == '1'
    regs = Registration.objects.filter(event=event, archived_at__isnull=not show_removed).select_related("person", "source_order_line__order", "credential").order_by("registration_number", "-created_at", "pk")

    # Filtros
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status")
    op_filter = request.GET.get("op_status")

    if q:
        cpf_digits = "".join(character for character in q if character.isascii() and character.isdigit())
        regs = regs.filter(
            Q(person__full_name__icontains=q) | Q(person__phone__icontains=q)
            | Q(team_name__icontains=q) | Q(person__city__icontains=q)
            | Q(registration_number__icontains=q)
            | Q(participant_cpf=cpf_digits if len(cpf_digits) == 11 else "invalid")
        )
    if status_filter:
        regs = regs.filter(commercial_state=status_filter)
    if op_filter:
        regs = regs.filter(operational_state=op_filter)

    page = Paginator(regs, 40).get_page(request.GET.get("page"))
    filters = {"q": q, "status": status_filter, "op_status": op_filter, "removed": '1' if show_removed else ''}

    return render(request, "gestao/registrations/registration_list.html", {
        "event": event,
        "registrations": page.object_list,
        "page_obj": page,
        "pagination_query": urlencode({key: value for key, value in filters.items() if value}),
        "q": q,
        "status_filter": status_filter,
        "op_filter": op_filter,
        "show_removed": show_removed,
    })


@staff_required_custom
@never_cache
def registration_detail(request, event_id, reg_id):
    event = get_object_or_404(Event, id=event_id)
    reg = get_object_or_404(
        Registration.objects.select_related("person", "source_order_line", "source_order_line__order", "credential"),
        id=reg_id,
        event=event
    )
    from apps.registrations.eligibility import credential_is_available
    entitlements = reg.entitlements.all()
    terms = reg.terms_acceptances.all()
    consumptions = Consumption.objects.filter(entitlement__registration=reg).select_related("operator", "entitlement")

    from apps.registrations.views_manual import manual_context
    response = render(request, "gestao/registrations/registration_detail.html", {
        "event": event,
        "reg": reg,
        "entitlements": entitlements,
        "terms": terms,
        "consumptions": consumptions,
        "credential_available": credential_is_available(reg),
        "recorded_refunds": reg.source_order_line.order.refunds.all(),
        "addon_orders": Order.objects.filter(event=event,lines__addon__isnull=False,lines__linked_registration_id=reg.pk).distinct().prefetch_related('lines','refunds').order_by('-created_at','-pk'),
        **manual_context(request, reg),
    })
    response["Referrer-Policy"] = "no-referrer"
    return response


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
            if reg.source_order_line.order.status in ('PAID','PARTIALLY_REFUNDED','REFUNDED') and new_status != old_status:
                messages.error(request, 'Uma inscrição com pagamento deve ser cancelada pela opção Cancelar e registrar devolução. A confirmação após pagamento é automática.')
                return redirect('gestao:registration_detail',event_id=event.id,reg_id=reg.id)
            if new_status == CommercialState.ACTIVE and reg.source_order_line.order.status != 'PAID':
                messages.error(request,'Confirme o recebimento pela opção de pagamento manual ou aguarde a aprovação do Mercado Pago.')
                return redirect('gestao:registration_detail',event_id=event.id,reg_id=reg.id)
            reg.commercial_state = new_status
            try:
                with transaction.atomic():
                    reg.save(update_fields=["commercial_state", "updated_at"])
            except IntegrityError:
                messages.error(request, "Este CPF já possui outra inscrição ou reserva neste evento. Não é possível reativar uma inscrição duplicada.")
                return redirect("gestao:registration_detail", event_id=event.id, reg_id=reg.id)
            log_audit(actor=request.user, event_id=event.id, action="REGISTRATION_STATUS_CHANGED", entity_type="Registration", entity_id=reg.id, diff={"old": old_status, "new": new_status}, reason=reason, request=request)
            messages.success(request, f"Situação da inscrição atualizada para {reg.get_commercial_state_display()}.")

    return redirect("gestao:registration_detail", event_id=event.id, reg_id=reg.id)


@staff_required_custom
@never_cache
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
@never_cache
def credenciamento_lookup_api(request, event_id):
    """
    API JSON que resolve código do QR ou busca rápida.
    Retorna apenas dados necessários para a conferência operacional (LGPD).
    """
    from apps.registrations.eligibility import eligibility_reason
    from apps.reporting.selectors import event_timezone
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    registrations = Registration.objects.filter(event_id=event_id).select_related(
        "person", "credential", "checkin_record", "event", "source_order_line__order")
    selected = request.GET.get("registration_id", "")
    code = request.GET.get("code", "").strip()
    if selected:
        try:
            reg = registrations.filter(pk=uuid.UUID(selected)).first()
        except (ValueError, TypeError, AttributeError):
            return JsonResponse({"found": False, "error": "Inscrição inválida."}, status=400)
    else:
        if not code or len(code) > 256:
            return JsonResponse({"found": False, "error": "Informe um nome, número ou código de até 256 caracteres."}, status=400)
        token = code
        if ":" in code:
            parts = code.split(":")
            if len(parts) != 3 or parts[0].upper() != "TRILHA" or parts[1] != request.management_event.slug:
                return JsonResponse({"found": False, "error": "Este QR não corresponde à edição selecionada."})
            token = parts[2]
        reg = registrations.filter(credential__raw_token__iexact=token).first()
        if reg is None and ":" not in code:
            number = token.removeprefix("#").strip()
            if number.isdecimal():
                matches = registrations.filter(registration_number=int(number)) if len(number) <= 10 and int(number) <= 2147483647 else registrations.none()
            elif len(token) < 2 or len(token) > 150:
                return JsonResponse({"found": False, "error": "Digite pelo menos duas letras do nome ou o número da inscrição."}, status=400)
            else:
                matches = registrations.filter(person__full_name__icontains=token)
            candidates = list(matches.order_by("registration_number", "person__full_name", "pk")[:11])
            if len(candidates) > 1:
                return JsonResponse({"found": False, "ambiguous": True, "more_results": len(candidates) > 10,
                    "matches": [{"registration_id": str(item.pk), "number": item.registration_number,
                        "name": item.person.full_name, "city": item.person.city,
                        "team": item.team_name or item.person.team_name or "", "category": item.category_name}
                        for item in candidates[:10]]})
            reg = candidates[0] if candidates else None
    if reg is None:
        return JsonResponse({"found": False, "error": "Participante não localizado neste evento."})

    has_checked_in = hasattr(reg, "checkin_record")
    blocked_reason = eligibility_reason(reg)
    eligible = not blocked_reason
    local_zone = event_timezone(reg.event)
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

    history = Consumption.objects.filter(entitlement__registration=reg).select_related("entitlement", "operator").order_by("-occurred_at", "-pk")[:20]
    return JsonResponse({
        "found": True,
        "registration_id": str(reg.id),
        "number": reg.registration_number,
        "name": reg.person.full_name,
        "team": reg.team_name or reg.person.team_name or "-",
        "category": reg.category_name,
        "city": reg.person.city,
        "shirt_size": reg.shirt_size,
        "commercial_state": reg.commercial_state,
        "is_active": reg.commercial_state == CommercialState.ACTIVE,
        "operational_state": reg.operational_state,
        "eligible": eligible,
        "blocked_reason": blocked_reason,
        "has_checked_in": has_checked_in,
        "checked_in_at": timezone.localtime(reg.checkin_record.occurred_at, local_zone).strftime("%d/%m/%Y %H:%M") if has_checked_in else None,
        "entitlements": entitlements_data,
        "delivery_history": [{"id": str(item.pk), "title": item.entitlement.title, "quantity": item.quantity,
            "action": item.action, "operator": item.operator.full_name or "Equipe",
            "occurred_at": timezone.localtime(item.occurred_at, local_zone).strftime("%d/%m/%Y %H:%M")} for item in history],
    })


@staff_required_custom
@never_cache
@transaction.atomic
def credenciamento_confirm_api(request, event_id):
    """
    Confirma presença (Check-in) com trava atômica.
    Idempotente: cliques repetidos não geram duplicatas.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from apps.registrations.eligibility import credential_is_available

    try:
        reg_id = uuid.UUID(request.POST.get("registration_id", ""))
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Inscrição inválida."}, status=400)
    reg = get_object_or_404(Registration.objects.select_for_update(), id=reg_id, event_id=event_id)
    if not credential_is_available(reg):
        return JsonResponse({"success": False, "error": "Inscrição não apta: confira pagamento, termos atuais e validade da credencial."}, status=409)

    source_key = f"CHECKIN-{event_id}-{reg.id}"
    checkin, created = CheckinRecord.objects.get_or_create(
        registration=reg,
        event_id=event_id,
        defaults={
            "operator": request.user,
            "source_key": source_key,
            "checkin_type": CheckinType.SCAN if request.POST.get("lookup_method") == "qr" else CheckinType.MANUAL_SEARCH,
            "occurred_at": timezone.now(),
        }
    )

    reg.operational_state = OperationalState.CHECKED_IN
    reg.save(update_fields=["operational_state", "updated_at"])

    return JsonResponse({
        "success": True,
        "already_checked_in": not created,
        "occurred_at": checkin.occurred_at.strftime("%d/%m %H:%M"),
    })


@staff_required_custom
@never_cache
def deliver_benefit_api(request, event_id):
    """
    Entrega benefício (Kit, Camiseta, Refeição) atômica com lock contra entrega duplicada.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from apps.registrations.eligibility import credential_is_available

    try:
        ent_id = uuid.UUID(request.POST.get("entitlement_id", ""))
        idempotency_key = uuid.UUID(request.POST.get("idempotency_key", ""))
        quantity = int(request.POST.get("quantity", "1"))
        if not 1 <= quantity <= 1000:
            raise ValueError
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
        previous = Consumption.objects.filter(entitlement=ent, source_key=source_key, action="DELIVERED").first()
        if previous:
            if previous.quantity != quantity:
                return JsonResponse({"success": False, "error": "Esta entrega já foi registrada com outra quantidade. Consulte o histórico."}, status=409)
            return JsonResponse({
                "success": True, "already_delivered": True,
                "remaining": ent.remaining, "quantity_used": ent.quantity_used,
                "is_consumed": ent.quantity_used >= ent.quantity_total,
            })

        if ent.state not in (EntitlementState.AVAILABLE, EntitlementState.PARTIALLY_USED) or not credential_is_available(reg):
            return JsonResponse({"success": False, "error": "Benefício indisponível ou inscrição não apta para retirada."}, status=409)

        if ent.remaining < quantity:
            return JsonResponse({
                "success": False,
                "error": "A quantidade solicitada supera o saldo disponível. Consulte a ficha novamente.",
                "already_consumed": ent.remaining == 0,
            }, status=409)

        Consumption.objects.create(
            entitlement=ent,
            quantity=quantity,
            action="DELIVERED",
            operator=request.user,
            source_key=source_key,
            occurred_at=timezone.now(),
        )

        ent.quantity_used += quantity
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
@never_cache
@require_GET
def catalog_management(request, event_id):
    event = get_object_or_404(Event, id=event_id)
    products = Product.objects.filter(event=event).prefetch_related("lots", "variants")
    coupons = Coupon.objects.filter(event=event).order_by("-valid_until")
    inventory = InventoryItem.objects.filter(event=event)
    from apps.checkout.addons import inventory_items

    return render(request, "gestao/catalog/catalog.html", {
        "event": event,
        "products": products,
        "coupons": coupons,
        "inventory": inventory,
        "addons": inventory_items(event, active=False),
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
    from apps.payments.models import Refund,RefundChannel,RefundState,ChargeAttempt
    date_form = DateRangeForm(request.GET)
    valid = date_form.is_valid()
    start = date_form.cleaned_data.get("date_start") if valid else None
    end = date_form.cleaned_data.get("date_end") if valid else None
    entries = financial_entries(event, start, end).order_by("-recognized_at", "-pk") if valid else FinancialEntry.objects.none()
    expenses = in_period(Expense.objects.filter(event=event).select_related("vendor"), "due_at", start, end, date_field=True).order_by("-due_at", "-pk") if valid else Expense.objects.none()
    form = expense_form if expense_form is not None else ExpenseForm(event=event)
    entries_page = Paginator(entries, 30).get_page(request.GET.get("entries_page"))
    expenses_page = Paginator(expenses, 20).get_page(request.GET.get("expenses_page"))
    addon_orders = in_period(Order.objects.filter(event=event,lines__addon__isnull=False).distinct(), 'created_at', start, end).prefetch_related('lines','refunds').order_by('-created_at','-pk') if valid else Order.objects.none()
    addon_orders_page = Paginator(addon_orders,20).get_page(request.GET.get('addon_orders_page'))
    return {
        "event": event, "kpi": get_event_kpi_summary(event),
        "finance": get_financial_summary(event, start, end) if valid else {},
        "entries": entries_page.object_list, "expenses": expenses_page.object_list,
        "entries_page": entries_page,
        "expenses_page": expenses_page,
        "addon_orders": addon_orders_page, "addon_orders_page": addon_orders_page,
        "legacy_refunds":Refund.objects.filter(order__event=event).filter(Q(channel=RefundChannel.UNKNOWN,state=RefundState.MANUAL_COMPLETED)|Q(order__charge_attempts__error_code='REFUND_CHANNEL_REVIEW')).distinct().select_related('order').order_by('-created_at')[:50],
        "refund_channel_pending":ChargeAttempt.objects.filter(order__event=event,error_code='REFUND_CHANNEL_REVIEW').exists(),
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
    form = ExpenseForm(request.POST, request.FILES, event=event)
    if form.is_valid():
        try:
            expense, created = create_expense(event=event, data=form.cleaned_data, actor=request.user, request=request)
        except ValidationError as error:
            form.add_error(None, error)
        except OSError:
            form.add_error(None,'Não foi possível salvar o comprovante. Tente novamente; a despesa não foi confirmada.')
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
@never_cache
@require_GET
def frame_management(request, event_id):
    event = request.management_event
    from apps.mediaframes.events_config import get_event_versions
    return render(request, "gestao/mediaframes/frame_list.html", {
        "event": event,
        "official_version": get_event_versions(event)[0],
        "frame_formats": [
            {"id": "story", "label": "Story", "dimensions": "1080 × 1920", "ratio": "9:16"},
            {"id": "feed", "label": "Feed", "dimensions": "1080 × 1350", "ratio": "4:5"},
        ],
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
@never_cache
def audit_log_view(request):
    logs = AuditLog.objects.all()
    if not is_administrator(request.user):
        logs = logs.filter(event_id__in=accessible_events(request.user, "audit").values("id"))
    event = None
    if request.GET.get("event_id") not in (None, "", "all"):
        event = selected_management_event(request, "audit")
        logs = logs.filter(event_id=event.pk)
    request.management_all_events = event is None
    query = request.GET.get("q", "").strip()
    if query:
        logs = logs.filter(Q(action__icontains=query) | Q(actor_email__icontains=query) |
                           Q(entity_type__icontains=query) | Q(entity_id__icontains=query) | Q(reason__icontains=query))
    page = Paginator(logs.order_by("-created_at", "-pk"), 40).get_page(request.GET.get("page"))
    return render(request, "gestao/audit/audit_log.html", {
        "logs": page.object_list, "page_obj": page, "q": query,
        "audit_event": event,
        "pagination_query": urlencode({"q": query, "event_id": str(event.pk) if event else "all"}),
    })
