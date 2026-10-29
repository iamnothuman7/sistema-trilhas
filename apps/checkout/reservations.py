"""Atomic reservation shared by public checkout and authorized internal registration."""
import secrets
import re
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from apps.events.models import Event
from apps.catalog.models import Product, Coupon, CapacityResource, CapacityHold, HoldStatus
from apps.catalog.offers import registration_catalog, available_registration_lots
from apps.identity.models import Person
from apps.registrations.models import Registration, CommercialState, OperationalState
from .models import Order, OrderLine, OrderStatus, OrderCoupon, ReceivableDue, DueType


@transaction.atomic
def reserve_order(*, event, participants, buyer, purchaser_user=None, coupon_code="", manual=False, check_budget=None):
    coupon_code = str(coupon_code or '').strip().upper()
    num_participants = len(participants)
    participant_cpfs = {participant["cpf"] for participant in participants}
    if not num_participants or len(participant_cpfs) != num_participants:
        raise ValidationError("Informe participantes distintos, com um CPF por inscrição.")
    # One lock also serializes initial capacity creation and registration numbering.
    event = Event.objects.select_for_update(of=("self",)).select_related("theme").get(pk=event.pk)
    if (not manual and not event.is_registration_open) or (manual and (event.status not in {"PUBLISHED", "SALES_CLOSED", "IN_PROGRESS"} or event.ends_at <= timezone.now())):
        raise ValidationError("As inscrições foram encerradas enquanto você preenchia os dados. Nenhum pedido foi criado.")
    products, active_lots, _ = registration_catalog(event)
    if Registration.objects.filter(event=event, participant_cpf__in=participant_cpfs,
            commercial_state__in=[CommercialState.RESERVED, CommercialState.ACTIVE]).exists():
        raise ValidationError("Já existe uma inscrição ou reserva para um dos CPFs neste evento. Acesse sua conta para acompanhar o pedido. Reservas não pagas são liberadas ao expirar.")
    for participant in participants:
        try:
            product = Product.objects.filter(id=participant["product_id"], event=event, active=True).first() if participant["product_id"] else (products[0] if products else None)
            valid_lots = available_registration_lots(event).filter(product=product)
            lot = valid_lots.filter(id=participant["lot_id"]).first() if participant["lot_id"] else valid_lots.first()
        except (ValueError, ValidationError):
            product = lot = None
        if not product or not lot or not lot.is_open:
            raise ValidationError("O pacote ou lote escolhido não está disponível. Revise a inscrição.")
        size_variants = {variant.code: variant for variant in product.variants.filter(kind="SHIRT_SIZE")}
        allowed_sizes = set(size_variants) or {"P", "M", "G", "GG", "XG"}
        if product.includes_shirt and participant["shirt"] not in allowed_sizes:
            raise ValidationError(f"Escolha um tamanho de camiseta disponível para {participant['name']}.")
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
    if cap_resource.capacity_total != event.max_attendees:
        cap_resource.capacity_total = event.max_attendees
        cap_resource.save(update_fields=["capacity_total"])

    if cap_resource.available_count < num_participants:
        raise ValidationError(f"Desculpe, restam apenas {cap_resource.available_count} vaga(s) disponível(is).")

    # 2. Criar o pedido
    ref_code = f"TM-{event.edition_number:02d}-{secrets.token_hex(3).upper()}"
    hold_expires = timezone.now() + timedelta(minutes=35)

    order = Order.objects.create(
        event=event,
        merchant_reference=ref_code,
        purchaser_user=purchaser_user,
        purchaser_name=buyer["name"],
        purchaser_email=buyer["email"],
        purchaser_phone=buyer["phone"],
        purchaser_document=buyer["document"],
        status=OrderStatus.RESERVED,
        expires_at=hold_expires,
        currency="BRL"
    )

    gross_total = 0
    requested_per_lot={}

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
        requested_per_lot[lot.pk]=requested_per_lot.get(lot.pk,0)+1
        if lot_capacity.available_count < requested_per_lot[lot.pk]:
            raise ValidationError("O lote escolhido não tem vagas suficientes para este grupo. Selecione outro lote.")
        lot_capacity.reserved_count += 1
        lot_capacity.save(update_fields=["capacity_total", "reserved_count"])
        price_cents = lot.price_cents + participant["extra_cents"]
        gross_total += price_cents

        # A name is not an identity: participants may be unrelated namesakes.
        person = Person.objects.create(
            full_name=p_name,
            document_number=participant["cpf"],
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
                "kind": product.kind,
                "product_name": product.name if product else "Inscrição Piloto",
                "lot_title": lot.title if lot else "Lote Único",
                "price_cents": price_cents,
                "lot_price_cents": lot.price_cents,
                "variant_extra_price_cents": participant["extra_cents"],
                "variant_code": participant["variant"].code if participant["variant"] else "",
                "variant_label": participant["variant"].label if participant["variant"] else "",
                "shirt_size": shirt,
                "vehicle_type": participant.get("vehicle", ""),
                "buyer_is_participant": bool(participant.get("is_buyer") and participant["cpf"] == re.sub(r"\D", "", buyer["document"])),
                "includes_kit": product.includes_kit,
                "includes_shirt": product.includes_shirt,
                "includes_meal": product.includes_meal,
            }
        )

        # Criar inscrição vinculada
        if product.includes_shirt:
            from apps.catalog.stock import reserve_shirt
            reserve_shirt(event=event,variant=participant['variant'],line=line,expires_at=hold_expires)
        Registration.objects.create(
            participant_cpf=participant["cpf"],
            event=event,
            person=person,
            source_order_line=line,
            category_name=product.name if product else "Piloto",
            team_name=p_team,
            shirt_size=shirt,
            vehicle_type=participant.get("vehicle", ""),
            commercial_state=CommercialState.RESERVED,
            operational_state=OperationalState.NOT_READY,
            product_snapshot_json=line.item_snapshot_json,
        )

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
        if not coupon or not coupon.is_valid():
            raise ValidationError('Este cupom não existe, está desativado, fora da validade ou esgotado. Revise o código ou remova o cupom para continuar sem desconto.')
        else:
            if coupon.kind == "PERCENT":
                discount_total = min(gross_total, gross_total * coupon.percent_off // 100)
            else:
                discount_total = min(gross_total, coupon.value_cents)

            coupon.used_count += 1
            coupon.save()

            OrderCoupon.objects.create(
                order=order,
                coupon=coupon,
                coupon_code_snapshot=coupon.code_upper,
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

    if check_budget:
        check_budget(num_participants)
    return order
