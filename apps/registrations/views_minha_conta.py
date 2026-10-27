import hashlib
import ipaddress
import json
import secrets
import uuid
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from apps.checkout.models import OrderStatus
from apps.core.utils import log_audit
from apps.identity.models import ClaimToken, Person, UserPersonLink
from .models import Registration, TermsAcceptance
from .eligibility import credential_is_available


def private_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = (
            "no-referrer" if response.get("Content-Type", "").startswith("application/pdf") else "same-origin"
        )
        return response
    return wrapped


def visible_registrations(user):
    linked_people = UserPersonLink.objects.filter(user=user).values("person_id")
    return Registration.objects.filter(
        Q(source_order_line__order__purchaser_user=user) | Q(person_id__in=linked_people)
    ).distinct()


def _registration_for_view(request, registration_id):
    return get_object_or_404(visible_registrations(request.user).select_related(
        "event", "event__theme", "person", "credential", "source_order_line__order"
    ), pk=registration_id)


def _terms_context(reg, user):
    from apps.payments.services import required_documents_for_registration
    documents = list(required_documents_for_registration(reg))
    owners = UserPersonLink.objects.filter(person_id=reg.person_id).values("user_id")
    accepted = set(reg.terms_acceptances.filter(accepting_user_id__in=owners).values_list("document_version_id", flat=True))
    all_accepted = bool(documents) and all(doc.pk in accepted for doc in documents)
    return {
        "required_docs": documents, "accepted_doc_ids": accepted, "all_accepted": all_accepted,
        "is_holder": UserPersonLink.objects.filter(user=user, person_id=reg.person_id).exists(),
        "can_share_claim": reg.source_order_line.order.purchaser_user_id == user.pk and not owners.exists(),
        "credential_available": credential_is_available(reg),
    }


def registration_journey(reg, user, terms=None):
    """Explain the next safe action without changing payment or admission state."""
    from apps.events.models import EventStatus
    from .models import CommercialState, OperationalState
    terms = terms if terms is not None else _terms_context(reg, user)
    order = reg.source_order_line.order
    detail_url = reverse("minha_conta:registration_detail", args=[reg.pk])
    journey = {"title": "Confira sua inscrição", "message": "Consulte os detalhes ou fale com a organização.",
               "url": detail_url, "label": "Ver inscrição", "icon": "arrow-right", "tone": "warning"}
    if reg.event.status in (EventStatus.CANCELED, EventStatus.ARCHIVED):
        journey.update(title="Evento indisponível", message="Este evento está cancelado ou arquivado.")
    elif reg.commercial_state in (CommercialState.CANCELED, CommercialState.TRANSFERRED):
        journey.update(title=reg.get_commercial_state_display(), message="Esta inscrição não libera acesso ao evento.")
    elif order.status in (OrderStatus.RESERVED, OrderStatus.AWAITING_PAYMENT):
        if order.is_expired():
            journey.update(title="Reserva expirada", message="Se você já pagou, fale com a organização antes de tentar novamente.")
        else:
            journey.update(title="Aguardando pagamento", message="Sua inscrição será confirmada quando o pagamento for aprovado.")
            if order.purchaser_user_id == user.pk:
                journey.update(url=reverse("checkout:order_payment", args=[order.pk]), label="Continuar pagamento", icon="credit-card")
            else:
                journey["message"] = "O comprador precisa concluir o pagamento deste pedido."
    elif order.status != OrderStatus.PAID or reg.commercial_state != CommercialState.ACTIVE:
        journey.update(title=order.get_status_display(), message="Consulte a situação do pedido com a organização.")
    elif terms["credential_available"]:
        journey.update(title="Tudo pronto para o evento", message="Apresente seu QR Code e um documento na retirada do kit.",
                       url=reverse("minha_conta:credential_view", args=[reg.pk]), label="Ver meu QR Code", icon="qr-code", tone="success")
        if reg.operational_state == OperationalState.CHECKED_IN:
            journey.update(title="Presença confirmada", message="Seu credenciamento foi realizado. Consulte os benefícios nos detalhes da inscrição.")
    elif terms["can_share_claim"]:
        journey.update(title="Pagamento confirmado", message="Confirme se esta inscrição é sua ou envie o acesso para quem vai participar.",
                       url=detail_url + "#participante", label="Confirmar participante", icon="user-round")
    elif not terms["required_docs"]:
        journey.update(title="Pagamento confirmado", message="A organização está preparando os termos. Seu QR Code será liberado após o aceite.")
    elif not terms["all_accepted"]:
        journey.update(title="Pagamento confirmado", message="Falta aceitar os termos do evento para liberar seu QR Code.",
                       url=detail_url + "#termos", label="Liberar meu QR Code", icon="qr-code")
        if not terms["is_holder"]:
            journey.update(message="O participante precisa aceitar os termos na própria conta.", label="Ver termos do participante", icon="file-check")
    else:
        journey.update(title="Pagamento e termos confirmados", message="A liberação do QR Code precisa ser conferida pela organização.")
    return journey


@private_page
@login_required
@require_GET
@never_cache
def dashboard(request):
    from apps.mediaframes.access import available_pilot_art
    art_ids = {reg.pk for reg in available_pilot_art(request.user)}
    registrations = visible_registrations(request.user).select_related(
        "event", "event__theme", "person", "credential", "source_order_line__order"
    ).order_by("-event__starts_at", "pk")
    for reg in registrations:
        reg.journey = registration_journey(reg, request.user)
        reg.pilot_art_available = reg.pk in art_ids
    return render(request, "registrations/minha_conta_dashboard.html", {"registrations": registrations})


@private_page
@login_required
@require_GET
@never_cache
def registration_detail(request, registration_id):
    reg = _registration_for_view(request, registration_id)
    from apps.mediaframes.access import authorized_pilot_art
    context = {"reg": reg, "entitlements": reg.entitlements.all(), **_terms_context(reg, request.user),
               "pilot_art_available": bool(authorized_pilot_art(request.user, reg.pk))}
    context["journey"] = registration_journey(reg, request.user, context)
    return render(request, "registrations/registration_detail.html", context)


@private_page
@login_required
@require_POST
@never_cache
def accept_terms(request, registration_id):
    from apps.payments.services import activate_registration_if_eligible, required_documents_for_registration
    # A buyer can see the group purchase, but each participant signs personally.
    with transaction.atomic():
        reg = get_object_or_404(Registration.objects.select_for_update(), pk=registration_id)
        if not UserPersonLink.objects.filter(user=request.user, person_id=reg.person_id).exists():
            return HttpResponseForbidden("Somente o titular vinculado pode aceitar os termos desta inscrição.")
        documents = list(required_documents_for_registration(reg))
        try:
            submitted = {uuid.UUID(value) for value in request.POST.getlist("document_version_id")}
        except (ValueError, TypeError, AttributeError):
            submitted = set()
        if not documents or submitted != {doc.pk for doc in documents}:
            from apps.mediaframes.access import authorized_pilot_art
            context = {"reg": reg, "entitlements": reg.entitlements.all(), **_terms_context(reg, request.user),
                       "pilot_art_available": bool(authorized_pilot_art(request.user, reg.pk)),
                       "terms_error": "Leia e confirme todos os documentos vigentes. Se a versão mudou, atualize esta página."}
            context["journey"] = registration_journey(reg, request.user, context)
            return render(request, "registrations/registration_detail.html", context, status=400)
        try:
            ip = str(ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")))
        except ValueError:
            ip = None
        now = timezone.now()
        new_documents = []
        for document in documents:
            evidence = {"registration": str(reg.pk), "document": str(document.pk), "sha256": document.sha256,
                        "user": str(request.user.pk), "accepted_at": now.isoformat(), "ip": ip}
            _, created = TermsAcceptance.objects.get_or_create(
                registration=reg, document_version=document, accepting_user=request.user,
                defaults={"evidence_hash": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
                          "ip_address": ip, "user_agent": request.META.get("HTTP_USER_AGENT", "")[:1000], "accepted_at": now},
            )
            if created:
                new_documents.append(str(document.pk))
        ready = activate_registration_if_eligible(reg)
        if new_documents:
            log_audit(actor=request.user, event_id=reg.event_id, action="TERMS_ACCEPTED", entity_type="Registration",
                      entity_id=reg.pk, diff={"documents": new_documents}, request=request)
    messages.success(request, "Aceite registrado. Sua credencial está disponível." if ready else
                     "Aceite registrado. A liberação também depende da confirmação do pagamento e da situação da inscrição.")
    return redirect("minha_conta:credential_view" if ready else "minha_conta:registration_detail", registration_id=reg.pk)


@private_page
@login_required
@require_GET
@never_cache
def credential_view(request, registration_id):
    reg = _registration_for_view(request, registration_id)
    if not _terms_context(reg, request.user)["credential_available"]:
        messages.warning(request, "A credencial depende do pagamento confirmado, do aceite dos termos vigentes e de uma inscrição válida.")
        return redirect("minha_conta:registration_detail", registration_id=reg.pk)
    return render(request, "registrations/credential_card.html", {"reg": reg})


@private_page
@require_GET
@never_cache
def registration_pdf(request, registration_id):
    """A holder, buyer session, or event operator may download a private snapshot."""
    from django.contrib.auth.views import redirect_to_login
    from apps.checkout.views import authorized_order
    from apps.core.permissions import can_manage_event
    authenticated = request.user.is_authenticated and request.user.is_active
    if request.user.is_authenticated and not request.user.is_active:
        return HttpResponseForbidden("Acesso indisponível para esta conta.")
    if not authenticated and not request.session.get("checkout_order_ids"):
        return redirect_to_login(request.get_full_path(), login_url="/login/")
    reg = get_object_or_404(Registration.objects.select_related(
        "event", "person", "credential", "source_order_line__order",
    ).prefetch_related("entitlements"), pk=registration_id)
    holder = authenticated and UserPersonLink.objects.filter(user=request.user, person_id=reg.person_id).exists()
    operator = authenticated and any(can_manage_event(request.user, reg.event, action)
                                    for action in ("registrations", "checkin"))
    if not holder and not operator:
        authorized_order(request, reg.source_order_line.order_id)
    from .pdf import build_registration_pdf
    content = build_registration_pdf(reg)
    response = HttpResponse(content, content_type="application/pdf")
    number = reg.registration_number or str(reg.pk)[:8]
    response["Content-Disposition"] = f'attachment; filename="comprovante-inscricao-{number}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _private_claim_response(request, template, context, status=200):
    response = render(request, template, context, status=status)
    response["Referrer-Policy"] = "same-origin"
    response["Cache-Control"] = "private, no-store"
    return response


@private_page
@login_required
@require_POST
@never_cache
def claim_own_registration(request, registration_id):
    """The authenticated buyer can personally claim an unowned paid ticket in place."""
    with transaction.atomic():
        reg = get_object_or_404(Registration.objects.select_for_update(of=("self",)).select_related(
            "source_order_line__order"), pk=registration_id,
            source_order_line__order__purchaser_user=request.user)
        Person.objects.select_for_update().get(pk=reg.person_id)
        if UserPersonLink.objects.filter(person_id=reg.person_id).exclude(user=request.user).exists():
            return HttpResponseForbidden("Esta inscrição já pertence a outro participante.")
        if request.POST.get("confirm_holder") != "yes":
            messages.error(request, "Marque a confirmação de que você é o participante desta inscrição.")
            return redirect(reverse("minha_conta:registration_detail", args=[reg.pk]) + "#participante")
        if reg.commercial_state != "ACTIVE" or reg.source_order_line.order.status != OrderStatus.PAID:
            return HttpResponseForbidden("A confirmação depende de uma inscrição ativa e de um pagamento aprovado.")
        _, created = UserPersonLink.objects.get_or_create(user=request.user, person_id=reg.person_id,
            defaults={"method": "BUYER_PERSONAL_CONFIRMATION", "verified_at": timezone.now()})
        ClaimToken.objects.filter(registration=reg, used_at__isnull=True).update(expires_at=timezone.now())
        if created:
            log_audit(actor=request.user, event_id=reg.event_id, action="REGISTRATION_CLAIMED",
                      entity_type="Registration", entity_id=reg.pk, diff={"method": "buyer_personal_confirmation"}, request=request)
    messages.success(request, "Inscrição vinculada. Agora leia e aceite os termos para liberar seu QR Code.")
    return redirect(reverse("minha_conta:registration_detail", args=[reg.pk]) + "#termos")


@private_page
@require_POST
@never_cache
def create_claim(request, registration_id):
    from apps.checkout.views import authorized_order
    initial = get_object_or_404(Registration.objects.select_related("source_order_line"), pk=registration_id)
    order = authorized_order(request, initial.source_order_line.order_id)
    with transaction.atomic():
        reg = Registration.objects.select_for_update().get(pk=initial.pk)
        Person.objects.select_for_update().get(pk=reg.person_id)
        if UserPersonLink.objects.filter(person_id=reg.person_id).exists():
            return _private_claim_response(request, "registrations/claim_link.html",
                {"reg": reg, "order": order, "claim_error": "Esta inscrição já foi vinculada a um titular. Fale com a organização para corrigir a titularidade."}, status=409)
        now = timezone.now()
        ClaimToken.objects.filter(registration=reg, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
        token = secrets.token_urlsafe(32)
        claim = ClaimToken.objects.create(registration=reg, hashed_token=hashlib.sha256(token.encode()).hexdigest(),
                                          expires_at=now + timedelta(days=7))
        log_audit(actor=request.user if request.user.is_authenticated else None, event_id=reg.event_id,
                  action="REGISTRATION_CLAIM_LINK_CREATED", entity_type="Registration", entity_id=reg.pk, request=request)
    claim_url = request.build_absolute_uri(reverse("minha_conta:claim_ticket", kwargs={"token": token}))
    return _private_claim_response(request, "registrations/claim_link.html",
                                  {"reg": reg, "order": order, "claim_url": claim_url, "claim_expires_at": claim.expires_at})


@private_page
@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def claim_ticket(request, token):
    if not 20 <= len(token) <= 128:
        return _private_claim_response(request, "registrations/claim_confirm.html", {"claim_error": "Link inválido."}, status=404)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    initial = get_object_or_404(ClaimToken.objects.select_related("registration", "registration__person", "registration__event"),
                                hashed_token=token_hash)
    context = {"reg": initial.registration}
    if request.method == "GET":
        if not initial.is_valid():
            return _private_claim_response(request, "registrations/claim_confirm.html", {**context, "claim_error": "Este link expirou ou já foi utilizado."}, status=410)
        if UserPersonLink.objects.filter(person_id=initial.registration.person_id).exclude(user=request.user).exists():
            return _private_claim_response(request, "registrations/claim_confirm.html", {**context, "claim_error": "Esta inscrição já possui outro titular vinculado."}, status=409)
        return _private_claim_response(request, "registrations/claim_confirm.html", context)
    with transaction.atomic():
        reg = Registration.objects.select_for_update().get(pk=initial.registration_id)
        # Legacy registrations may share a Person; serialize claims for that person too.
        Person.objects.select_for_update().get(pk=reg.person_id)
        claim = ClaimToken.objects.select_for_update().get(pk=initial.pk)
        if not claim.is_valid():
            return _private_claim_response(request, "registrations/claim_confirm.html", {**context, "claim_error": "Este link expirou ou já foi utilizado."}, status=410)
        if UserPersonLink.objects.filter(person_id=reg.person_id).exclude(user=request.user).exists():
            return _private_claim_response(request, "registrations/claim_confirm.html", {**context, "claim_error": "Esta inscrição já possui outro titular vinculado."}, status=409)
        if request.POST.get("confirm_holder") != "yes":
            return _private_claim_response(request, "registrations/claim_confirm.html", {**context, "claim_error": "Confirme que você é o participante indicado."}, status=400)
        UserPersonLink.objects.get_or_create(user=request.user, person_id=reg.person_id,
                                             defaults={"method": "CLAIM_LINK", "verified_at": timezone.now()})
        claim.used_at = timezone.now()
        claim.save(update_fields=["used_at"])
        ClaimToken.objects.filter(registration=reg, used_at__isnull=True).update(expires_at=timezone.now())
        log_audit(actor=request.user, event_id=reg.event_id, action="REGISTRATION_CLAIMED", entity_type="Registration",
                  entity_id=reg.pk, request=request)
    messages.success(request, "Inscrição vinculada à sua conta. Você já pode consultar e aceitar os termos do evento.")
    return redirect("minha_conta:registration_detail", registration_id=reg.pk)
