"""Visão geral do sorteio e controle de vendedores (venda digital presencial e repasses)."""
import secrets
from datetime import date
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.permissions import can_manage_event, management_required
from apps.finance.forms import parse_amount_cents
from .models_raffles import MotorcycleRaffle, RaffleSalesPoint, RaffleSellerSettlement, RaffleTicket
from .raffle_results import eligible_tickets
from .raffle_sales import (MAX_PER_SALE, price_cents, price_options, record_seller_sale, record_settlement,
                           seller_sales_blockers, seller_summaries, void_seller_sale, whatsapp_message, online_remaining)


def _raffle(request):
    return get_object_or_404(MotorcycleRaffle.objects.select_related("event"), event=request.management_event)


def _digits(phone):
    digits = "".join(ch for ch in phone or "" if ch.isdigit())
    if digits and not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


@management_required
@never_cache
@require_GET
def raffle_overview(request, event_id):
    event = request.management_event
    raffle = MotorcycleRaffle.objects.filter(event=event).first()
    if not raffle:
        return redirect("gestao:raffle_management", event_id=event.pk)
    eligible = eligible_tickets(raffle)
    by_source = dict(eligible.values_list("source").annotate(n=Count("id")))
    sellers = seller_summaries(raffle)
    checklist = [
        ("Prêmios e fotos", bool(raffle.motorcycle_model and raffle.primary_image), "Informe as motos e envie as fotos."),
        ("Preço do número", bool(raffle.quota_price_cents), "Defina o preço avulso e, se quiser, os pacotes."),
        ("Regulamento", bool(raffle.rules_text.strip()), "Escreva as regras do sorteio."),
        ("Data do sorteio", bool(raffle.draw_at and raffle.draw_at > timezone.now()) or hasattr(raffle, "result"), "Marque a data e o horário."),
        ("Autorização do sorteio", bool(raffle.authorization_reference.strip()), "Informe o número da autorização oficial."),
        ("Página pública", raffle.is_public, "Publique a apresentação do sorteio."),
    ]
    return render(request, "gestao/raffles/overview.html", {
        "event": event, "raffle": raffle, "saved_raffle": True, "checklist": checklist,
        "checklist_done": sum(1 for item in checklist if item[1]),
        "total_eligible": eligible.count(),
        "from_registrations": by_source.get("REGISTRATION", 0), "from_site": by_source.get("ONLINE", 0),
        "from_sellers": by_source.get("SELLER", 0), "from_paper": by_source.get("PAPER", 0),
        "site_revenue": eligible.filter(source="ONLINE").aggregate(s=Sum("amount_cents"))["s"] or 0,
        "seller_revenue": sum(s.sold_cents for s in sellers), "seller_pending": sum(max(0, s.pending_cents) for s in sellers),
        "seller_count": sum(1 for s in sellers if s.active),
        "site_open": raffle.purchase_available, "site_pending": raffle.sales_pending_requirements,
        "seller_blockers": seller_sales_blockers(raffle), "online_remaining": online_remaining(raffle),
        "options": price_options(raffle),
    })


@management_required
@never_cache
@require_http_methods(["GET", "POST"])
def raffle_sellers(request, event_id):
    event = request.management_event
    raffle = _raffle(request)
    can_finance = can_manage_event(request.user, event, "finance")
    status, last_sale = 200, None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add_seller":
                name, contact = request.POST.get("name", "").strip(), request.POST.get("contact", "").strip()
                if not name or len(name) > 120 or len(contact) > 150:
                    raise ValidationError("Informe o nome do vendedor (até 120 caracteres) e um contato de até 150.")
                seller, created = RaffleSalesPoint.objects.get_or_create(raffle=raffle, name=name, defaults={"contact": contact})
                if not created and not seller.active:
                    seller.active = True
                    seller.save(update_fields=["active"])
                messages.success(request, f"Vendedor {seller.name} {'cadastrado' if created else 'reativado'}.")
            elif action == "sale":
                seller = get_object_or_404(RaffleSalesPoint, raffle=raffle, pk=request.POST.get("seller"))
                quantity = int(request.POST.get("quantity", "0"))
                reference, numbers, total = record_seller_sale(raffle=raffle, seller=seller, buyer_name=request.POST.get("buyer_name", ""),
                    buyer_phone=request.POST.get("buyer_phone", ""), quantity=quantity, actor=request.user, request=request)
                request.session["raffle_last_sale"] = reference
                messages.success(request, f"Venda registrada: {len(numbers)} número(s) para {request.POST.get('buyer_name','').strip()}.")
            elif action == "settle":
                if not can_finance:
                    raise ValidationError("Registrar repasse exige acesso ao financeiro.")
                seller = get_object_or_404(RaffleSalesPoint, raffle=raffle, pk=request.POST.get("seller"))
                received = date.fromisoformat(request.POST.get("received_on", ""))
                if received > timezone.localdate():
                    raise ValidationError("A data do repasse não pode estar no futuro.")
                _, created = record_settlement(seller=seller, amount_cents=parse_amount_cents(request.POST.get("amount")), received_on=received,
                    method=request.POST.get("method", "PIX"), notes=request.POST.get("notes", ""),
                    request_key=request.POST.get("request_key", "")[:64] or secrets.token_hex(16), actor=request.user, request=request)
                messages.success(request, "Repasse registrado e lançado no caixa do evento." if created else "Este repasse já estava registrado.")
            elif action == "void_sale":
                numbers = void_seller_sale(raffle=raffle, reference=request.POST.get("sale", ""), reason=request.POST.get("reason", ""),
                                           actor=request.user, request=request)
                messages.success(request, f"Venda cancelada. Números {', '.join(map(str, numbers))} saíram do sorteio e não serão reutilizados.")
            elif action == "toggle_seller":
                seller = get_object_or_404(RaffleSalesPoint, raffle=raffle, pk=request.POST.get("seller"))
                seller.active = not seller.active
                seller.save(update_fields=["active"])
                messages.success(request, f"Vendedor {seller.name} {'reativado' if seller.active else 'desativado'}. As vendas anteriores continuam valendo.")
            else:
                raise ValidationError("Operação inválida.")
        except (ValidationError, ValueError, TypeError) as exc:
            status = 400
            messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "Revise os valores informados.")
        else:
            target = request.POST.get("return_to") or ""
            if target.startswith(f"/gestao/eventos/{event.pk}/sorteio/"):
                return redirect(target)
            return redirect("gestao:raffle_sellers", event_id=event.pk)
    reference = request.session.pop("raffle_last_sale", None)
    if reference:
        tickets = list(raffle.tickets.filter(sale_reference=reference).order_by("number"))
        if tickets:
            numbers = [t.number for t in tickets]
            text = whatsapp_message(raffle, tickets[0].buyer_name, numbers)
            last_sale = {"numbers": numbers, "buyer": tickets[0].buyer_name, "total": sum(t.amount_cents for t in tickets),
                         "seller": tickets[0].point, "text": text, "whatsapp": f"https://wa.me/{_digits(tickets[0].buyer_phone)}?text={quote(text)}"
                         if _digits(tickets[0].buyer_phone) else ""}
    sellers = seller_summaries(raffle)
    return render(request, "gestao/raffles/sellers.html", {
        "event": event, "raffle": raffle, "saved_raffle": True, "sellers": sellers, "active_sellers": [s for s in sellers if s.active],
        "blockers": seller_sales_blockers(raffle), "options": price_options(raffle), "max_per_sale": MAX_PER_SALE,
        "price_table": {q: price_cents(raffle, q) for q in range(1, MAX_PER_SALE + 1)} if raffle.quota_price_cents else {},
        "last_sale": last_sale, "can_finance": can_finance, "today": timezone.localdate().isoformat(), "request_key": secrets.token_hex(16),
        "totals": {"sold": sum(s.sold_cents for s in sellers), "settled": sum(s.settled_cents for s in sellers),
                   "pending": sum(s.pending_cents for s in sellers), "numbers": sum(s.numbers for s in sellers)},
    }, status=status)


@management_required
@never_cache
@require_GET
def raffle_seller_detail(request, event_id, point_id):
    event = request.management_event
    raffle = _raffle(request)
    seller = get_object_or_404(RaffleSalesPoint, raffle=raffle, pk=point_id)
    summary = next(s for s in seller_summaries(raffle) if s.pk == seller.pk)
    sales = {}
    for ticket in raffle.tickets.filter(point=seller, source=RaffleTicket.Source.SELLER).select_related("recorded_by").order_by("-created_at", "number"):
        sale = sales.setdefault(ticket.sale_reference, {"reference": ticket.sale_reference, "buyer": ticket.buyer_name, "phone": ticket.buyer_phone,
            "created_at": ticket.created_at, "numbers": [], "total": 0, "void": ticket.state == RaffleTicket.State.VOID,
            "recorded_by": ticket.recorded_by})
        sale["numbers"].append(ticket.number)
        sale["total"] += ticket.amount_cents
    for sale in sales.values():
        sale["numbers"].sort()
        if not sale["void"] and _digits(sale["phone"]):
            sale["whatsapp"] = f"https://wa.me/{_digits(sale['phone'])}?text={quote(whatsapp_message(raffle, sale['buyer'], sale['numbers']))}"
    return render(request, "gestao/raffles/seller_detail.html", {
        "event": event, "raffle": raffle, "saved_raffle": True, "seller": summary, "sales": list(sales.values()),
        "settlements": RaffleSellerSettlement.objects.filter(seller=seller).select_related("recorded_by"),
        "can_finance": can_manage_event(request.user, event, "finance"), "today": timezone.localdate().isoformat(),
        "request_key": secrets.token_hex(16), "locked": hasattr(raffle, "result"),
    })
