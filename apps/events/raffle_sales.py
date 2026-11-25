"""Preço, abertura e venda digital de números da sorte (site, inscrição e vendedores).

Todas as rotas de venda usam as mesmas regras daqui: preço por pacotes, limite de
números e as condições para a venda estar aberta.
"""
import uuid
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.utils import log_audit
from apps.finance.models import FinancialEntry, FinancialEntryKind
from .models_raffles import MotorcycleRaffle, RaffleSalesPoint, RaffleSellerSettlement, RaffleTicket

MAX_PER_SALE = 100
SUGGESTED_UNIT_CENTS = 2000
SUGGESTED_PACKAGES = [{"qty": 5, "cents": 8000}, {"qty": 10, "cents": 15000}, {"qty": 15, "cents": 21000}, {"qty": 20, "cents": 26000}]


def packages(raffle):
    rows = []
    for item in raffle.package_prices or []:
        try:
            qty, cents = int(item["qty"]), int(item["cents"])
        except (KeyError, TypeError, ValueError):
            continue
        if 2 <= qty <= MAX_PER_SALE and cents > 0:
            rows.append({"qty": qty, "cents": cents})
    return sorted(rows, key=lambda row: row["qty"])


def parse_packages(text):
    """'5=80,00; 10=150,00' -> [{'qty': 5, 'cents': 8000}, ...]"""
    rows, seen = [], set()
    for chunk in [c.strip() for c in (text or "").replace("\n", ";").split(";") if c.strip()]:
        if "=" not in chunk:
            raise ValidationError(f"Use o formato quantidade=valor, como 5=80,00. Revise “{chunk}”.")
        qty_raw, price_raw = [part.strip() for part in chunk.split("=", 1)]
        try:
            qty = int(qty_raw)
            price = Decimal(price_raw.replace("R$", "").replace(".", "").replace(",", ".").strip())
        except (ValueError, InvalidOperation):
            raise ValidationError(f"Não entendi o pacote “{chunk}”. Exemplo: 5=80,00.") from None
        if not 2 <= qty <= MAX_PER_SALE or price <= 0:
            raise ValidationError(f"Cada pacote precisa de 2 a {MAX_PER_SALE} números e valor maior que zero.")
        if qty in seen:
            raise ValidationError(f"O pacote de {qty} números aparece duas vezes.")
        seen.add(qty)
        rows.append({"qty": qty, "cents": int((price * 100).quantize(Decimal("1")))})
    return sorted(rows, key=lambda row: row["qty"])


def format_packages(rows):
    return "; ".join(f"{row['qty']}={row['cents'] // 100},{row['cents'] % 100:02d}" for row in rows)


def price_cents(raffle, quantity):
    """Menor valor para a quantidade, combinando pacotes e números avulsos."""
    unit = raffle.quota_price_cents
    if not unit or quantity < 1:
        raise ValidationError("O preço do número ainda não foi definido.")
    best = [0] + [None] * quantity
    options = [(1, unit)] + [(row["qty"], row["cents"]) for row in packages(raffle)]
    for total in range(1, quantity + 1):
        for size, cost in options:
            if size <= total and best[total - size] is not None:
                candidate = best[total - size] + cost
                if best[total] is None or candidate < best[total]:
                    best[total] = candidate
    return best[quantity]


def price_options(raffle):
    """Opções para a página pública: avulso e pacotes, com a economia em relação ao avulso."""
    unit = raffle.quota_price_cents
    if not unit:
        return []
    rows = [{"qty": 1, "cents": unit, "economy_cents": 0}]
    for row in packages(raffle):
        rows.append({**row, "economy_cents": max(0, row["qty"] * unit - row["cents"])})
    return rows


def online_remaining(raffle):
    if not raffle.quota_quantity:
        return None
    from apps.catalog.models import CapacityResource
    resource = CapacityResource.objects.filter(event_id=raffle.event_id, type="RAFFLE_TOTAL", reference_id=str(raffle.pk)).first()
    used = (resource.reserved_count + resource.committed_count) if resource else 0
    return max(0, raffle.quota_quantity - used)


def reserve_online_capacity(raffle, quantity):
    """Chame dentro de transaction.atomic. Bloqueia o estoque de números online."""
    from apps.catalog.models import CapacityResource
    resource, _ = CapacityResource.objects.select_for_update().get_or_create(
        event_id=raffle.event_id, type="RAFFLE_TOTAL", reference_id=str(raffle.pk),
        defaults={"capacity_total": raffle.quota_quantity or 10000})
    if raffle.quota_quantity and resource.capacity_total != raffle.quota_quantity:
        resource.capacity_total = raffle.quota_quantity
    if raffle.quota_quantity and resource.reserved_count + resource.committed_count + quantity > raffle.quota_quantity:
        raise ValidationError("Não há mais números disponíveis para venda online nesta quantidade.")
    resource.reserved_count += quantity
    resource.save(update_fields=["reserved_count", "capacity_total"])
    return resource


def seller_sales_blockers(raffle):
    pending = []
    if hasattr(raffle, "result"):
        pending.append("O resultado já foi registrado")
    if not raffle.quota_price_cents:
        pending.append("Preço do número")
    if not raffle.rules_text.strip():
        pending.append("Regulamento")
    if not raffle.authorization_reference.strip():
        pending.append("Número da autorização do sorteio")
    if not raffle.draw_at or raffle.draw_at <= timezone.now():
        pending.append("Data futura do sorteio")
    return pending


def next_number(raffle, used=None):
    """Próximo número livre. A faixa em papel só é pulada se ainda existirem bilhetes em papel."""
    from django.db.models import Max
    number = (raffle.tickets.exclude(source="PAPER").aggregate(n=Max("number"))["n"] or 0) + 1
    paper = raffle.tickets.filter(source="PAPER").exists()
    taken = set(used or ())
    while (paper and raffle.paper_start <= number <= raffle.paper_end) or number in taken or raffle.tickets.filter(number=number).exists():
        number = raffle.paper_end + 1 if paper and raffle.paper_start <= number <= raffle.paper_end else number + 1
    return number


@transaction.atomic
def record_seller_sale(*, raffle, seller, buyer_name, buyer_phone, quantity, actor, request=None):
    from .models import Event
    Event.objects.select_for_update().get(pk=raffle.event_id)
    raffle = MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
    blockers = seller_sales_blockers(raffle)
    if blockers:
        raise ValidationError("A venda ainda não pode abrir. Falta: " + ", ".join(blockers) + ".")
    seller = RaffleSalesPoint.objects.select_for_update().get(pk=seller.pk, raffle=raffle)
    if not seller.active:
        raise ValidationError("Este vendedor está desativado.")
    buyer_name, buyer_phone = buyer_name.strip(), buyer_phone.strip()
    if not buyer_name or len(buyer_name) > 150 or len(buyer_phone) > 30:
        raise ValidationError("Informe o nome do comprador (até 150 caracteres) e um WhatsApp válido.")
    if not 1 <= quantity <= MAX_PER_SALE:
        raise ValidationError(f"Cada venda pode ter de 1 a {MAX_PER_SALE} números.")
    total = price_cents(raffle, quantity)
    reference = uuid.uuid4().hex[:12].upper()
    now = timezone.now()
    tickets, used = [], []
    for index in range(quantity):
        number = next_number(raffle, used)
        used.append(number)
        # Divide o total sem perder centavos: os primeiros números absorvem o resto.
        share = total // quantity + (1 if index < total % quantity else 0)
        tickets.append(RaffleTicket(raffle=raffle, number=number, source=RaffleTicket.Source.SELLER, state=RaffleTicket.State.SOLD,
            point=seller, buyer_name=buyer_name, buyer_phone=buyer_phone, amount_cents=share, paid_at=now,
            recorded_by=actor, sale_reference=reference))
    RaffleTicket.objects.bulk_create(tickets)
    log_audit(actor=actor, event_id=raffle.event_id, action="RAFFLE_SELLER_SALE", entity_type="RaffleSalesPoint", entity_id=seller.pk,
        diff={"sale": reference, "numbers": used, "amount_cents": total}, request=request)
    return reference, used, total


@transaction.atomic
def void_seller_sale(*, raffle, reference, reason, actor, request=None):
    from .models import Event
    Event.objects.select_for_update().get(pk=raffle.event_id)
    raffle = MotorcycleRaffle.objects.select_for_update().get(pk=raffle.pk)
    if hasattr(raffle, "result"):
        raise ValidationError("O sorteio já foi apurado. As vendas ficam preservadas.")
    if len(reason.strip()) < 10:
        raise ValidationError("Explique o motivo do cancelamento (pelo menos 10 caracteres).")
    tickets = list(RaffleTicket.objects.select_for_update().filter(raffle=raffle, source=RaffleTicket.Source.SELLER,
                                                                   sale_reference=reference).exclude(state=RaffleTicket.State.VOID))
    if not tickets:
        raise ValidationError("Venda não encontrada ou já cancelada.")
    RaffleTicket.objects.filter(pk__in=[t.pk for t in tickets]).update(state=RaffleTicket.State.VOID)
    log_audit(actor=actor, event_id=raffle.event_id, action="RAFFLE_SELLER_SALE_VOIDED", entity_type="RaffleSalesPoint",
        entity_id=tickets[0].point_id, reason=reason.strip(), diff={"sale": reference, "numbers": [t.number for t in tickets]}, request=request)
    return [t.number for t in tickets]


@transaction.atomic
def record_settlement(*, seller, amount_cents, received_on, method, notes, request_key, actor, request=None):
    existing = RaffleSellerSettlement.objects.filter(request_key=request_key).first()
    if existing:
        return existing, False
    seller = RaffleSalesPoint.objects.select_for_update().select_related("raffle").get(pk=seller.pk)
    if amount_cents <= 0:
        raise ValidationError("Informe um valor maior que zero.")
    entry = FinancialEntry.objects.create(event_id=seller.raffle.event_id, raffle=seller.raffle, kind=FinancialEntryKind.RECEITA_SORTEIO,
        amount_cents_signed=amount_cents, source_reference=f"RAFFLE-SELLER-{request_key}"[:120], posted_by=actor,
        recognized_at=timezone.make_aware(datetime.combine(received_on, time(12))),
        description=f"Repasse do vendedor {seller.name} — sorteio"[:255])
    settlement = RaffleSellerSettlement.objects.create(seller=seller, amount_cents=amount_cents, received_on=received_on, method=method,
        notes=notes.strip()[:300], ledger_entry=entry, request_key=request_key, recorded_by=actor)
    log_audit(actor=actor, event_id=seller.raffle.event_id, action="RAFFLE_SELLER_SETTLEMENT", entity_type="RaffleSalesPoint",
        entity_id=seller.pk, diff={"amount_cents": amount_cents}, request=request)
    return settlement, True


def seller_summaries(raffle):
    sold = Q(tickets__state=RaffleTicket.State.SOLD, tickets__source=RaffleTicket.Source.SELLER)
    rows = list(raffle.sales_points.annotate(numbers=Count("tickets", filter=sold), sold_cents=Sum("tickets__amount_cents", filter=sold))
                .order_by("-active", "name"))
    settled = dict(RaffleSellerSettlement.objects.filter(seller__raffle=raffle).values_list("seller").annotate(s=Sum("amount_cents")))
    for row in rows:
        row.sold_cents = row.sold_cents or 0
        row.settled_cents = settled.get(row.pk, 0)
        row.pending_cents = row.sold_cents - row.settled_cents
    return rows


def whatsapp_message(raffle, buyer_name, numbers):
    first = buyer_name.split()[0] if buyer_name.strip() else ""
    when = timezone.localtime(raffle.draw_at).strftime("%d/%m/%Y às %H:%M") if raffle.draw_at else "data a confirmar"
    label = "Seu número da sorte" if len(numbers) == 1 else "Seus números da sorte"
    return (f"Olá{', ' + first if first else ''}! {label} no {raffle.title} ({raffle.event.title}): "
            f"{', '.join(str(n) for n in numbers)}. Sorteio: {when}. Boa sorte!")
