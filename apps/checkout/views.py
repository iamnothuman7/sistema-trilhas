import uuid
import secrets
import re
from functools import wraps
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.cache import never_cache
from apps.events.models import Event, EventStatus
from apps.catalog.models import Product, Lot, ProductVariant, Coupon, CapacityResource, CapacityHold, HoldStatus
from apps.catalog.offers import available_registration_lots, registration_catalog
from apps.checkout.models import Order, OrderLine, OrderStatus, OrderCoupon, ReceivableDue, DueType
from apps.payments.models import ChargeAttempt, ChargeState, PaymentMethod
from apps.payments.gateway import MercadoPagoGateway
from apps.identity.models import Person
from apps.registrations.models import Registration, CommercialState, OperationalState


def private_checkout(view):
    """Keep purchaser data, reservation IDs and payment form tokens out of caches/referrers."""
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response
    return wrapped


def _registration_context(request, event, products, active_lots):
    buyer = {}
    for field, user_field in (("name", "full_name"), ("email", "email"), ("phone", "phone"), ("document", "")):
        buyer[field] = request.POST.get(f"purchaser_{field}", "") if request.method == "POST" else (
            getattr(request.user, user_field, "") if request.user.is_authenticated and user_field else "")
    rows = []
    names = request.POST.getlist("participant_name[]") if request.method == "POST" else []
    for index, name in enumerate(names):
        row = {"name": name}
        for field in ("email", "phone", "city", "state", "team", "product", "lot", "shirt"):
            values = request.POST.getlist(f"participant_{field}[]")
            row[field] = values[index] if index < len(values) else ""
        rows.append(row)
    if not rows and request.method == "POST" and request.POST.get("participant_name") is not None:
        rows = [{field: request.POST.get("participant_"+field, "") for field in ("name", "email", "phone", "city", "state", "team")}]
        rows[0].update(product=request.POST.get("product_id", ""), lot=request.POST.get("lot_id", ""), shirt=request.POST.get("shirt_size", "M"))
    if not rows:
        selected = request.GET.get("product", "")
        if selected not in {str(product.pk) for product in products}:
            selected = str(products[0].pk) if products else ""
        rows = [{"city": event.city, "state": event.state, "shirt": "M", "product": selected}]
    shirt_options = []
    for product in products:
        variants = [variant for variant in product.variants.all() if variant.kind == "SHIRT_SIZE"]
        sizes = [(variant.code, variant.label, variant.extra_price_cents) for variant in variants] if variants else [(code, code, 0) for code in ("P", "M", "G", "GG", "XG")]
        if not product.includes_shirt:
            sizes = [("", "Esta inscrição não inclui camiseta", 0)]
        shirt_options.extend({"product_id": str(product.pk), "code": code, "label": label, "extra_cents": extra} for code, label, extra in sizes)
    return {"event": event, "products": products, "active_lots": active_lots, "shirt_options": shirt_options,
            "buyer": buyer, "participant_rows": rows, "coupon_code": request.POST.get("coupon_code", ""),
            "payments_available": not MercadoPagoGateway().configuration_issues()}


@private_checkout
def start_registration(request, event_slug):
    """
    Inicia o fluxo de checkout e inscrição para 1 ou N participantes (individual ou grupo).
    Cria reservas temporárias com SELECT FOR UPDATE para evitar oversell.
    """
    event = get_object_or_404(Event.objects.select_related("theme"), slug=event_slug)

    if not event.is_registration_open:
        messages.error(request, "As inscrições para este evento estão fechadas no momento.")
        return redirect("events:detail", slug=event.slug)

    # Run outside the reservation transaction: expiry locks Order before resources.
    from apps.payments.services import expire_stale_orders
    expire_stale_orders(event_id=event.pk)

    products, active_lots, _ = registration_catalog(event)

    if request.method == "POST":
        purchaser_name = request.POST.get("purchaser_name", "").strip()
        purchaser_email = request.POST.get("purchaser_email", "").strip().lower()
        purchaser_phone = request.POST.get("purchaser_phone", "").strip()
        purchaser_doc = request.POST.get("purchaser_document", "").strip()
        coupon_code = request.POST.get("coupon_code", "").strip().upper()

        # Participantes enviados (suporta individual e múltiplos)
        # Nomes: participant_name[], emails: participant_email[], etc.
        participant_names = request.POST.getlist("participant_name[]")
        participant_emails = request.POST.getlist("participant_email[]")
        participant_phones = request.POST.getlist("participant_phone[]")
        participant_cities = request.POST.getlist("participant_city[]")
        participant_states = request.POST.getlist("participant_state[]")
        participant_teams = request.POST.getlist("participant_team[]")
        participant_products = request.POST.getlist("participant_product[]")
        participant_lots = request.POST.getlist("participant_lot[]")
        participant_shirts = request.POST.getlist("participant_shirt[]")

        # Se veio formato simples (1 participante)
        if not participant_names and request.POST.get("participant_name"):
            participant_names = [request.POST.get("participant_name")]
            participant_emails = [request.POST.get("participant_email", purchaser_email)]
            participant_phones = [request.POST.get("participant_phone", purchaser_phone)]
            participant_cities = [request.POST.get("participant_city", "Tianguá")]
            participant_states = [request.POST.get("participant_state", "CE")]
            participant_teams = [request.POST.get("participant_team", "")]
            participant_products = [request.POST.get("product_id")]
            participant_lots = [request.POST.get("lot_id")]
            participant_shirts = [request.POST.get("shirt_size", "M")]

        if not purchaser_name or not purchaser_email or not purchaser_phone or not participant_names:
            messages.error(request, "Por favor, preencha todos os campos obrigatórios do comprador e dos pilotos.")
            return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))

        errors = []
        try:
            validate_email(purchaser_email)
            if len(purchaser_email) > 254:
                raise ValidationError("E-mail muito longo.")
        except ValidationError:
            errors.append("Informe um e-mail válido para o responsável pela inscrição.")
        if len(purchaser_name) > 150 or len(purchaser_phone) > 30 or len(purchaser_doc) > 20:
            errors.append("Revise os dados do responsável: nome até 150 caracteres, telefone até 30 e documento até 20.")
        participants = []
        for index, raw_name in enumerate(participant_names):
            def value(values, default=""):
                return ((values[index] or "") if index < len(values) else default).strip()
            participant = {"name": raw_name.strip(), "email": value(participant_emails).lower(),
                "phone": value(participant_phones), "city": value(participant_cities, event.city),
                "state": value(participant_states, event.state).upper(), "team": value(participant_teams),
                "product_id": value(participant_products), "lot_id": value(participant_lots),
                "shirt": value(participant_shirts, "M")}
            label = f"Participante {index+1}"
            if not participant["name"] or len(participant["name"]) > 150:
                errors.append(f"{label}: informe um nome com até 150 caracteres.")
            if participant["email"]:
                try:
                    validate_email(participant["email"])
                    if len(participant["email"]) > 254:
                        raise ValidationError("E-mail muito longo.")
                except ValidationError:
                    errors.append(f"{label}: informe um e-mail válido ou deixe esse campo em branco.")
            if participant["state"] and not re.fullmatch(r"[A-Z]{2}", participant["state"]):
                errors.append(f"{label}: informe a UF com duas letras, como CE.")
            if any(len(participant[field]) > limit for field, limit in (("phone", 30), ("city", 100), ("team", 120), ("shirt", 30))):
                errors.append(f"{label}: revise o tamanho dos campos de telefone, cidade, equipe e camiseta.")
            participants.append(participant)
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))

        num_participants = len(participant_names)

        with transaction.atomic():
            # One lock also serializes initial capacity creation and registration numbering.
            event = Event.objects.select_for_update(of=("self",)).select_related("theme").get(pk=event.pk)
            if not event.is_registration_open:
                messages.error(request, "As inscrições foram encerradas enquanto você preenchia os dados. Nenhum pedido foi criado.")
                return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))
            products, active_lots, _ = registration_catalog(event)
            for participant in participants:
                try:
                    product = Product.objects.filter(id=participant["product_id"], event=event, active=True).first() if participant["product_id"] else (products[0] if products else None)
                    valid_lots = available_registration_lots(event).filter(product=product)
                    lot = valid_lots.filter(id=participant["lot_id"]).first() if participant["lot_id"] else valid_lots.first()
                except (ValueError, ValidationError):
                    product = lot = None
                if not product or not lot or not lot.is_open:
                    messages.error(request, "O pacote ou lote escolhido não está disponível. Revise a inscrição.")
                    return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))
                size_variants = {variant.code: variant for variant in product.variants.filter(kind="SHIRT_SIZE")}
                allowed_sizes = set(size_variants) or {"P", "M", "G", "GG", "XG"}
                if product.includes_shirt and participant["shirt"] not in allowed_sizes:
                    messages.error(request, f"Escolha um tamanho de camiseta disponível para {participant['name']}.")
                    return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))
                if not product.includes_shirt:
                    participant["shirt"] = ""
                variant = size_variants.get(participant["shirt"]) if product.includes_shirt else None
                participant.update(product=product, lot=lot, variant=variant,
                                   extra_cents=variant.extra_price_cents if variant else 0)
            # 1. Travar recurso de capacidade geral
            cap_resource, _ = CapacityResource.objects.select_for_update().get_or_create(
                event=event,
                type="EVENT_OVERALL",
                defaults={"capacity_total": event.max_attendees}
            )

            if cap_resource.available_count < num_participants:
                messages.error(request, f"Desculpe, restam apenas {cap_resource.available_count} vaga(s) disponível(is).")
                return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))

            # 2. Criar o pedido
            ref_code = f"TM-{event.edition_number:02d}-{secrets.token_hex(3).upper()}"
            hold_expires = timezone.now() + timedelta(minutes=35)

            order = Order.objects.create(
                event=event,
                merchant_reference=ref_code,
                purchaser_user=request.user if request.user.is_authenticated else None,
                purchaser_name=purchaser_name,
                purchaser_email=purchaser_email,
                purchaser_phone=purchaser_phone,
                purchaser_document=purchaser_doc,
                status=OrderStatus.RESERVED,
                expires_at=hold_expires,
                currency="BRL"
            )

            gross_total = 0
            next_registration_number = (Registration.objects.filter(event=event).aggregate(number=Max("registration_number"))["number"] or 0) + 1

            # 3. Criar as linhas do pedido e inscrições
            for participant in participants:
                p_name, p_email, p_phone = participant["name"], participant["email"], participant["phone"]
                p_city, p_state, p_team = participant["city"], participant["state"], participant["team"]
                product, lot, shirt = participant["product"], participant["lot"], participant["shirt"]
                lot_capacity, _ = CapacityResource.objects.select_for_update().get_or_create(
                    event=event, type="LOT_TOTAL", reference_id=str(lot.id),
                    defaults={"capacity_total": lot.max_quantity, "committed_count": lot.current_sold})
                if lot_capacity.capacity_total != lot.max_quantity:
                    lot_capacity.capacity_total = lot.max_quantity
                if lot_capacity.available_count < 1:
                    messages.error(request, "O lote escolhido não tem vagas suficientes para este grupo. Selecione outro lote.")
                    result = render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))
                    transaction.set_rollback(True)
                    return result
                lot_capacity.reserved_count += 1
                lot_capacity.save(update_fields=["capacity_total", "reserved_count"])
                price_cents = lot.price_cents + participant["extra_cents"]
                gross_total += price_cents

                # A name is not an identity: participants may be unrelated namesakes.
                person = Person.objects.create(
                    full_name=p_name,
                    email=p_email or None,
                    phone=p_phone or None,
                    city=p_city,
                    state=p_state,
                    team_name=p_team,
                )

                line = OrderLine.objects.create(
                    order=order,
                    product=product,
                    lot=lot,
                    variant=participant["variant"],
                    intended_person_name=p_name,
                    intended_person_email=p_email,
                    intended_person_phone=p_phone,
                    intended_person_city=p_city,
                    intended_person_state=p_state,
                    intended_person_team=p_team,
                    intended_variant_code=shirt,
                    quantity=1,
                    unit_cents=price_cents,
                    net_cents=price_cents,
                    item_snapshot_json={
                        "product_name": product.name if product else "Inscrição Piloto",
                        "lot_title": lot.title if lot else "Lote Único",
                        "price_cents": price_cents,
                        "lot_price_cents": lot.price_cents,
                        "variant_extra_price_cents": participant["extra_cents"],
                        "variant_code": participant["variant"].code if participant["variant"] else "",
                        "variant_label": participant["variant"].label if participant["variant"] else "",
                        "shirt_size": shirt,
                        "includes_kit": product.includes_kit,
                        "includes_shirt": product.includes_shirt,
                        "includes_meal": product.includes_meal,
                    }
                )

                # Criar inscrição vinculada
                Registration.objects.create(
                    event=event,
                    person=person,
                    source_order_line=line,
                    registration_number=next_registration_number,
                    category_name=product.name if product else "Piloto",
                    team_name=p_team,
                    shirt_size=shirt,
                    commercial_state=CommercialState.RESERVED,
                    operational_state=OperationalState.NOT_READY,
                    product_snapshot_json=line.item_snapshot_json,
                )
                next_registration_number += 1

                # Travar recurso de capacidade
                CapacityHold.objects.create(
                    resource=cap_resource,
                    order_id=order.id,
                    order_line_id=line.id,
                    quantity=1,
                    status=HoldStatus.ACTIVE,
                    expires_at=hold_expires,
                    idempotency_key=f"HOLD-{order.id}-{line.id}"
                )
                CapacityHold.objects.create(resource=lot_capacity, order_id=order.id, order_line_id=line.id,
                    quantity=1, status=HoldStatus.ACTIVE, expires_at=hold_expires,
                    idempotency_key=f"HOLD-LOT-{order.id}-{line.id}")

            # Atualizar contador reservado no banco
            cap_resource.reserved_count += num_participants
            cap_resource.save()

            # 4. Aplicar cupom se houver
            discount_total = 0
            if coupon_code:
                coupon = Coupon.objects.select_for_update().filter(event=event, code_upper=coupon_code).first()
                if coupon and coupon.is_valid():
                    if coupon.kind == "PERCENT":
                        discount_total = min(gross_total, gross_total * coupon.percent_off // 100)
                    else:
                        discount_total = min(gross_total, coupon.value_cents)

                    coupon.used_count += 1
                    coupon.save()

                    OrderCoupon.objects.create(
                        order=order,
                        coupon=coupon,
                        discount_applied_cents=discount_total
                    )

            net_total = max(0, gross_total - discount_total)
            order.gross_cents = gross_total
            order.discount_cents = discount_total
            order.total_cents = net_total
            order.save()

            # Ratear desconto entre as linhas
            if discount_total > 0 and num_participants > 0:
                lines = list(order.lines.order_by("pk"))
                discounts = [discount_total * line.unit_cents // gross_total for line in lines]
                remaining = discount_total - sum(discounts)
                ranking = sorted(range(len(lines)), key=lambda i: (discount_total * lines[i].unit_cents) % gross_total, reverse=True)
                for index in ranking[:remaining]:
                    discounts[index] += 1
                for line, allocated in zip(lines, discounts):
                    line.allocated_discount_cents = allocated
                    line.net_cents = line.unit_cents - allocated
                    line.save()

            # Criar obrigação de pagamento integral (ReceivableDue)
            ReceivableDue.objects.create(
                order=order,
                due_type=DueType.FULL,
                due_at=hold_expires,
                amount_cents=net_total,
                status="PENDING"
            )

        authorized_ids = request.session.get("checkout_order_ids", [])
        request.session["checkout_order_ids"] = [str(value) for value in authorized_ids[-49:]] + [str(order.id)]
        return redirect("checkout:order_payment", order_id=order.id)

    return render(request, "checkout/registration_form.html", _registration_context(request, event, products, active_lots))


def authorized_order(request, order_id):
    """Buyer authorization is independent of knowing a public/order identifier."""
    session_ids = request.session.get("checkout_order_ids", [])
    if not isinstance(session_ids, list):
        session_ids = []
    access = Q(pk__in=session_ids)
    if request.user.is_authenticated:
        access |= Q(purchaser_user=request.user)
    return get_object_or_404(Order.objects.select_related("event", "event__theme").prefetch_related(
        "lines", "lines__registration", "lines__registration__person").filter(access), pk=order_id)


def _payment_context(order):
    from decimal import Decimal
    from django.core import signing
    from django.urls import reverse
    gateway = MercadoPagoGateway()
    return {
        "order": order,
        "payments_available": order.total_cents > 0 and not gateway.configuration_issues(),
        "card_available": order.total_cents > 0 and not gateway.configuration_issues(card=True),
        "zero_total": order.total_cents <= 0,
        "attempt_key": signing.dumps({"order": str(order.id), "key": str(uuid.uuid4())}, salt="checkout-payment"),
        "mp_config": {
            "publicKey": gateway.public_key,
            "amount": format(Decimal(order.total_cents) / 100, ".2f"),
            "email": order.purchaser_email,
            "processUrl": reverse("checkout:process_payment", kwargs={"order_id": order.id}),
        },
    }


@private_checkout
def order_payment(request, order_id):
    from apps.payments.services import expire_order
    order = authorized_order(request, order_id)
    if order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT}:
        return redirect("checkout:order_result", order_id=order.id)
    if order.is_expired():
        expire_order(order.id)
        messages.error(request, "O prazo da reserva terminou. Fale com a organização se você já efetuou o pagamento.")
        return redirect("checkout:order_result", order_id=order.id)
    if order.charge_attempts.filter(state__in=[ChargeState.SENT, ChargeState.PENDING, ChargeState.UNKNOWN]).exists():
        return redirect("checkout:order_waiting", order_id=order.id)
    return render(request, "checkout/payment_options.html", _payment_context(order))


@private_checkout
@sensitive_post_parameters("card_token", "identification_number")
@sensitive_variables("card_data")
@require_POST
def process_payment_attempt(request, order_id):
    from django.conf import settings
    from django.core import signing
    from apps.payments.gateway import GatewayError
    from apps.payments.services import submit_charge, PaymentValidationError, expire_order
    order = authorized_order(request, order_id)
    method = request.POST.get("payment_method", "")
    if method not in {PaymentMethod.PIX, PaymentMethod.CREDIT_CARD}:
        return HttpResponseBadRequest("Meio de pagamento inválido.")
    try:
        submitted = signing.loads(request.POST.get("attempt_key", ""), salt="checkout-payment", max_age=3600)
        if submitted.get("order") != str(order.id):
            raise ValueError
        key = str(uuid.UUID(submitted["key"]))
    except (signing.BadSignature, ValueError, KeyError, TypeError, AttributeError):
        messages.error(request, "Atualize a página antes de iniciar o pagamento.")
        return redirect("checkout:order_payment", order_id=order.id)
    gateway = MercadoPagoGateway()
    try:
        gateway.require_configuration(card=method == PaymentMethod.CREDIT_CARD)
    except GatewayError as exc:
        messages.error(request, str(exc))
        return redirect("checkout:order_payment", order_id=order.id)
    card_data = {}
    if method == PaymentMethod.CREDIT_CARD:
        try:
            installments = int(request.POST.get("installments", "1"))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Parcelamento inválido.")
        card_data = {"token": request.POST.get("card_token", ""),
                     "payment_method_id": request.POST.get("payment_method_id", ""),
                     "installments": installments,
                     "issuer_id": request.POST.get("issuer_id", ""),
                     "identification_type": request.POST.get("identification_type", ""),
                     "identification_number": request.POST.get("identification_number", "")}
        if not card_data["token"] or not card_data["payment_method_id"] or not 1 <= installments <= 12:
            messages.error(request, "Preencha o formulário seguro do cartão antes de continuar.")
            return redirect("checkout:order_payment", order_id=order.id)
    with transaction.atomic():
        # Share the activation lock before creating the first charge: a concurrent
        # account switch must either finish first or observe this attempt afterward.
        from apps.payments.models import MercadoPagoConnection
        connection, _ = MercadoPagoConnection.objects.get_or_create(pk=1)
        MercadoPagoConnection.objects.select_for_update().get(pk=connection.pk)
        try:
            MercadoPagoGateway().require_configuration(card=method == PaymentMethod.CREDIT_CARD)
        except GatewayError as exc:
            messages.error(request, str(exc))
            return redirect("checkout:order_payment", order_id=order.id)
        order = Order.objects.select_for_update().select_related("event").get(pk=order_id)
        if order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT}:
            return redirect("checkout:order_result", order_id=order.id)
        if order.is_expired():
            expire_order(order.id)
            return redirect("checkout:order_result", order_id=order.id)
        if order.total_cents <= 0:
            messages.error(request, "Este pedido precisa ser confirmado pela organização sem cobrança online.")
            return redirect("checkout:order_payment", order_id=order.id)
        existing = order.charge_attempts.filter(idempotency_key=key).first()
        unresolved = order.charge_attempts.filter(state__in=[ChargeState.NEW, ChargeState.SENT, ChargeState.PENDING, ChargeState.UNKNOWN, ChargeState.SUCCEEDED]).first()
        if unresolved:
            return redirect("checkout:order_waiting", order_id=order.id)
        if existing:
            messages.error(request, existing.error_message or "Esta tentativa já foi processada. Atualize a página para tentar novamente.")
            return redirect("checkout:order_payment", order_id=order.id)
        expiry = None
        if method == PaymentMethod.PIX:
            # Payments API requires at least 30 minutes. One minute covers request latency.
            expiry = timezone.now() + timedelta(minutes=max(31, min(settings.PIX_EXPIRES_MINUTES, 30 * 24 * 60)))
            order.expires_at = max(order.expires_at, expiry + timedelta(minutes=2))
            CapacityHold.objects.filter(order_id=order.id, status=HoldStatus.ACTIVE).update(expires_at=order.expires_at)
        charge = ChargeAttempt.objects.create(order=order, idempotency_key=key, payment_method=method,
            state=ChargeState.SENT, amount_cents=order.total_cents,
            pix_expiration=expiry if method == PaymentMethod.PIX else None)
        order.status = OrderStatus.AWAITING_PAYMENT
        order.save(update_fields=["status", "expires_at", "updated_at"])
    # Never hold a database transaction open while waiting for the external provider.
    try:
        submit_charge(charge, **card_data)
    except GatewayError as exc:
        messages.error(request, str(exc))
    except PaymentValidationError:
        messages.error(request, "A confirmação do pagamento está em análise. Não pague novamente; fale com a organização.")
    charge.refresh_from_db()
    order.refresh_from_db()
    if order.status in {OrderStatus.PAID, OrderStatus.PAID_NEEDS_REVIEW, OrderStatus.REFUNDED, OrderStatus.PARTIALLY_REFUNDED}:
        return redirect("checkout:order_result", order_id=order.id)
    if charge.state in {ChargeState.FAILED, ChargeState.CANCELED}:
        messages.error(request, charge.error_message or "O pagamento não foi aprovado. Confira os dados e tente novamente.")
        return redirect("checkout:order_payment", order_id=order.id)
    return redirect("checkout:order_waiting", order_id=order.id)


@private_checkout
def order_waiting(request, order_id):
    order = authorized_order(request, order_id)
    charge = order.charge_attempts.first()
    if order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT}:
        return redirect("checkout:order_result", order_id=order.id)
    if not charge:
        return redirect("checkout:order_payment", order_id=order.id)
    if charge.state in {ChargeState.FAILED, ChargeState.CANCELED}:
        return redirect("checkout:order_payment", order_id=order.id)
    return render(request, "checkout/waiting_pix.html", {"order": order, "charge": charge})


@private_checkout
def order_status_api(request, order_id):
    from django.urls import reverse
    order = authorized_order(request, order_id)
    charge = order.charge_attempts.first()
    terminal = order.status not in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT}
    retry = charge and charge.state in {ChargeState.FAILED, ChargeState.CANCELED}
    redirect_url = None
    if terminal or retry:
        redirect_url = reverse("checkout:order_result" if terminal else "checkout:order_payment", kwargs={"order_id": order.id})
    response = JsonResponse({"status": order.status, "payment_state": charge.state if charge else None,
        "is_paid": order.status == OrderStatus.PAID, "redirect_url": redirect_url})
    response["Cache-Control"] = "no-store"
    return response


@private_checkout
@require_POST
def order_refresh_payment(request, order_id):
    """Recover a delayed notification by reading the provider; never create another charge."""
    from apps.payments.services import refresh_charge, PaymentValidationError
    from apps.payments.gateway import GatewayError
    order = authorized_order(request, order_id)
    charge = order.charge_attempts.first()
    message = "A situação do pedido está atualizada."
    if order.status in {OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT} and charge:
        if MercadoPagoGateway().configuration_issues():
            message = "A consulta ao Mercado Pago está indisponível. Fale com a organização e não pague novamente."
        else:
            now = timezone.now()
            claimed = ChargeAttempt.objects.filter(pk=charge.pk, state__in=[ChargeState.SENT, ChargeState.PENDING, ChargeState.UNKNOWN]).filter(
                Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=now-timedelta(seconds=30))
            ).update(last_checked_at=now)
            if claimed:
                try:
                    refresh_charge(charge)
                    message = "Consulta concluída. Se ainda estiver pendente, aguarde a confirmação; não é necessário pagar novamente."
                except GatewayError:
                    message = "Não foi possível consultar agora. Tente novamente em alguns instantes, sem fazer outro pagamento."
                except PaymentValidationError:
                    message = "A confirmação precisa de conferência pela organização. Não faça outro pagamento."
            else:
                message = "Já consultamos há poucos segundos. Aguarde 30 segundos para uma nova consulta."
    response = order_status_api(request, order_id)
    import json
    data = json.loads(response.content)
    data["message"] = message
    return JsonResponse(data)


@private_checkout
def order_result(request, order_id):
    order = authorized_order(request, order_id)
    provider_verified = order.charge_attempts.filter(transactions__fulfilled_at__isnull=False,
        transactions__provider_status="approved", transactions__posted_refund_cents=0).exists()
    return render(request, "checkout/order_result.html", {
        "order": order, "is_paid": order.status == OrderStatus.PAID, "provider_verified": provider_verified,
    })
