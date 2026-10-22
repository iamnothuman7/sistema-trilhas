import uuid
import secrets
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.db import transaction
from django.utils import timezone
from django.contrib import messages
from apps.events.models import Event, EventStatus
from apps.catalog.models import Product, Lot, ProductVariant, Coupon, CapacityResource, CapacityHold, HoldStatus
from apps.checkout.models import Order, OrderLine, OrderStatus, OrderCoupon, ReceivableDue, DueType
from apps.payments.models import ChargeAttempt, ChargeState, PaymentMethod
from apps.payments.gateway import MercadoPagoGateway
from apps.identity.models import Person
from apps.registrations.models import Registration, CommercialState, OperationalState

def start_registration(request, event_slug):
    """
    Inicia o fluxo de checkout e inscrição para 1 ou N participantes (individual ou grupo).
    Cria reservas temporárias com SELECT FOR UPDATE para evitar oversell.
    """
    event = get_object_or_404(Event.objects.select_related("theme"), slug=event_slug)

    if not event.is_registration_open:
        messages.error(request, "As inscrições para este evento estão fechadas no momento.")
        return redirect("events:detail", slug=event.slug)

    products = Product.objects.filter(event=event, active=True).prefetch_related("lots", "variants")
    active_lots = Lot.objects.filter(event=event, active=True).order_by("priority", "opens_at")

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
            return render(request, "checkout/registration_form.html", {
                "event": event, "products": products, "active_lots": active_lots
            })

        num_participants = len(participant_names)

        with transaction.atomic():
            # 1. Travar recurso de capacidade geral
            cap_resource, _ = CapacityResource.objects.select_for_update().get_or_create(
                event=event,
                type="EVENT_OVERALL",
                defaults={"capacity_total": event.max_attendees}
            )

            if cap_resource.available_count < num_participants:
                messages.error(request, f"Desculpe, restam apenas {cap_resource.available_count} vaga(s) disponível(is).")
                return render(request, "checkout/registration_form.html", {
                    "event": event, "products": products, "active_lots": active_lots
                })

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

            # 3. Criar as linhas do pedido e inscrições
            for i in range(num_participants):
                p_name = participant_names[i].strip()
                p_email = (participant_emails[i] if i < len(participant_emails) else "").strip()
                p_phone = (participant_phones[i] if i < len(participant_phones) else "").strip()
                p_city = (participant_cities[i] if i < len(participant_cities) else "Tianguá").strip()
                p_state = (participant_states[i] if i < len(participant_states) else "CE").strip()
                p_team = (participant_teams[i] if i < len(participant_teams) else "").strip()
                prod_id = participant_products[i] if i < len(participant_products) else None
                lot_id = participant_lots[i] if i < len(participant_lots) else None
                shirt = (participant_shirts[i] if i < len(participant_shirts) else "M").strip()

                product = Product.objects.filter(id=prod_id, event=event).first() if prod_id else products.first()
                lot = Lot.objects.filter(id=lot_id, event=event).first() if lot_id else active_lots.first()

                price_cents = lot.price_cents if lot else 18000
                gross_total += price_cents

                # Criar ou localizar a pessoa
                person, _ = Person.objects.get_or_create(
                    full_name=p_name,
                    defaults={
                        "email": p_email or None,
                        "phone": p_phone or None,
                        "city": p_city,
                        "state": p_state,
                        "team_name": p_team,
                    }
                )

                line = OrderLine.objects.create(
                    order=order,
                    product=product,
                    lot=lot,
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
                        "shirt_size": shirt,
                    }
                )

                # Criar inscrição vinculada
                last_num = Registration.objects.filter(event=event).count()
                Registration.objects.create(
                    event=event,
                    person=person,
                    source_order_line=line,
                    registration_number=last_num + 1 + i,
                    category_name=product.name if product else "Piloto",
                    team_name=p_team,
                    shirt_size=shirt,
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

            # Atualizar contador reservado no banco
            cap_resource.reserved_count += num_participants
            cap_resource.save()

            # 4. Aplicar cupom se houver
            discount_total = 0
            if coupon_code:
                coupon = Coupon.objects.filter(event=event, code_upper=coupon_code).first()
                if coupon and coupon.is_valid():
                    if coupon.kind == "PERCENT":
                        discount_total = int(gross_total * (coupon.percent_off / 100.0))
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
                discount_per_line = discount_total // num_participants
                for line in order.lines.all():
                    line.allocated_discount_cents = discount_per_line
                    line.net_cents = max(0, line.unit_cents - discount_per_line)
                    line.save()

            # Criar obrigação de pagamento integral (ReceivableDue)
            ReceivableDue.objects.create(
                order=order,
                due_type=DueType.FULL,
                due_at=hold_expires,
                amount_cents=net_total,
                status="PENDING"
            )

        return redirect("checkout:order_payment", order_id=order.id)

    return render(request, "checkout/registration_form.html", {
        "event": event,
        "products": products,
        "active_lots": active_lots,
    })


def order_payment(request, order_id):
    """
    Tela de revisão do pedido e seleção do meio de pagamento (Pix ou Cartão).
    """
    order = get_object_or_404(
        Order.objects.select_related("event", "event__theme").prefetch_related("lines"),
        id=order_id
    )

    if order.status == OrderStatus.PAID:
        return redirect("checkout:order_result", order_id=order.id)

    if order.is_expired():
        order.status = OrderStatus.EXPIRED
        order.save()
        messages.error(request, "O tempo de reserva para este pedido expirou.")
        return redirect("events:detail", slug=order.event.slug)

    gateway = MercadoPagoGateway()

    return render(request, "checkout/payment_options.html", {
        "order": order,
        "mp_public_key": gateway.public_key,
    })


def process_payment_attempt(request, order_id):
    """
    Inicia a tentativa de cobrança com Pix ou Cartão de Crédito.
    """
    order = get_object_or_404(Order.objects.select_related("event"), id=order_id)
    method = request.POST.get("payment_method", "pix")
    gateway = MercadoPagoGateway()

    if method == "pix":
        charge = ChargeAttempt.objects.filter(order=order, payment_method="pix").first()
        if not charge:
            key = f"ATTEMPT-PIX-{order.id}-{uuid.uuid4().hex[:6]}"
            pix_res = gateway.create_pix_order(order, idempotency_key=key)

            charge = ChargeAttempt.objects.create(
                order=order,
                provider="mercadopago",
                provider_order_id=pix_res.get("provider_order_id"),
                provider_payment_ref=pix_res.get("provider_payment_ref"),
                idempotency_key=key,
                payment_method=PaymentMethod.PIX,
                state=ChargeState.PENDING,
                amount_cents=order.total_cents,
                pix_qr_code=pix_res.get("qr_code_base64"),
                pix_copy_paste=pix_res.get("copy_paste"),
                pix_expiration=pix_res.get("expires_at"),
            )
            order.status = OrderStatus.AWAITING_PAYMENT
            order.save()

        return redirect("checkout:order_waiting", order_id=order.id)

    elif method == "credit_card":
        token = request.POST.get("card_token")
        installments = int(request.POST.get("installments", 1))
        key = f"ATTEMPT-CARD-{order.id}-{uuid.uuid4().hex[:6]}"

        card_res = gateway.create_card_charge(order, token=token, installments=installments, idempotency_key=key)

        charge = ChargeAttempt.objects.create(
            order=order,
            provider="mercadopago",
            provider_order_id=card_res.get("provider_order_id"),
            provider_payment_ref=card_res.get("provider_payment_ref"),
            idempotency_key=key,
            payment_method=PaymentMethod.CREDIT_CARD,
            state=ChargeState.SUCCEEDED if card_res.get("success") else ChargeState.FAILED,
            amount_cents=order.total_cents,
            card_brand=card_res.get("card_brand", "cartao"),
            card_last_four=card_res.get("card_last_four", "0000"),
            card_installments=card_res.get("card_installments", installments),
        )

        if card_res.get("success"):
            # Liquida o pedido diretamente
            from apps.payments.views import mercadopago_webhook
            order.status = OrderStatus.PAID
            order.save()
            charge.state = ChargeState.SUCCEEDED
            charge.resolved_at = timezone.now()
            charge.save()

            # Ativa inscrições
            for line in order.lines.select_related("registration"):
                if hasattr(line, "registration"):
                    reg = line.registration
                    reg.commercial_state = CommercialState.ACTIVE
                    reg.save()

            return redirect("checkout:order_result", order_id=order.id)
        else:
            messages.error(request, f"Pagamento não aprovado pelo cartão: {card_res.get('error_message', 'Tente outro meio de pagamento.')}")
            return redirect("checkout:order_payment", order_id=order.id)

    return redirect("checkout:order_payment", order_id=order.id)


def order_waiting(request, order_id):
    """
    Página com o QR Code Pix e o Copia-e-Cola com polling automático de confirmação.
    """
    order = get_object_or_404(Order.objects.select_related("event", "event__theme"), id=order_id)
    charge = ChargeAttempt.objects.filter(order=order, payment_method="pix").order_by("-sent_at").first()

    if order.status == OrderStatus.PAID:
        return redirect("checkout:order_result", order_id=order.id)

    return render(request, "checkout/waiting_pix.html", {
        "order": order,
        "charge": charge,
    })


def order_status_api(request, order_id):
    """
    Endpoint JSON chamado via JavaScript a cada 3 segundos na tela de aguardando Pix.
    """
    order = get_object_or_404(Order.objects, id=order_id)
    return JsonResponse({
        "status": order.status,
        "is_paid": order.status == OrderStatus.PAID,
        "redirect_url": f"/checkout/pedido/{order.id}/resultado/" if order.status == OrderStatus.PAID else None,
    })


def order_result(request, order_id):
    """
    Tela final de confirmação de pagamento com comprovante, dados de acesso e orientações.
    """
    order = get_object_or_404(
        Order.objects.select_related("event", "event__theme").prefetch_related("lines", "lines__registration", "lines__registration__person"),
        id=order_id
    )

    return render(request, "checkout/order_result.html", {
        "order": order,
    })
